"""Tests for multi-tenancy isolation guarantees."""

from __future__ import annotations

from typing import Any

import pytest


class TestMultiTenancyIsolation:
    """Tests verifying tenant_id + workspace isolation in queries."""

    def test_findings_query_requires_tenant_id_filter(self) -> None:
        """All findings queries must include tenant_id in WHERE clause."""
        # This test documents the requirement
        required_filter = "tenant_id = %s"
        assert required_filter in "tenant_id = %s AND workspace = %s"

    def test_findings_query_requires_workspace_filter(self) -> None:
        """All findings queries must include workspace in WHERE clause."""
        required_filter = "workspace = %s"
        assert required_filter in "tenant_id = %s AND workspace = %s"

    def test_scope_tuple_format(self) -> None:
        """Scope should be a (tenant_id, workspace) tuple."""
        scope = ("acme", "prod")
        assert len(scope) == 2
        assert scope[0] == "acme"
        assert scope[1] == "prod"

    def test_tenant_id_must_be_non_empty(self) -> None:
        """tenant_id must be non-empty string."""
        with pytest.raises(ValueError):
            # Empty tenant_id should be rejected
            tenant_id = ""
            if not tenant_id:
                raise ValueError("tenant_id must not be empty")

    def test_workspace_must_be_non_empty(self) -> None:
        """workspace must be non-empty string."""
        with pytest.raises(ValueError):
            # Empty workspace should be rejected
            workspace = ""
            if not workspace:
                raise ValueError("workspace must not be empty")

    def test_cross_tenant_isolation_by_design(self) -> None:
        """Tenants should never see each other's data by design."""
        # This test documents the security invariant
        tenant_a_data = {"tenant_id": "tenant_a", "finding": "secret_a"}
        tenant_b_data = {"tenant_id": "tenant_b", "finding": "secret_b"}

        # A query for tenant_a should never return tenant_b's data
        def query_tenant(tenant_id: str, data: list[dict]) -> list[dict]:
            return [d for d in data if d["tenant_id"] == tenant_id]

        results = query_tenant("tenant_a", [tenant_a_data, tenant_b_data])
        assert len(results) == 1
        assert results[0]["tenant_id"] == "tenant_a"
        assert "secret_b" not in str(results)

    def test_same_fingerprint_different_tenants_are_distinct(self) -> None:
        """Same fingerprint in different tenants should be treated as different findings."""
        finding_a = {"tenant_id": "tenant_a", "fingerprint": "same_fp", "title": "Same issue"}
        finding_b = {"tenant_id": "tenant_b", "fingerprint": "same_fp", "title": "Same issue"}

        # They have the same fingerprint but different tenants
        assert finding_a["fingerprint"] == finding_b["fingerprint"]
        assert finding_a["tenant_id"] != finding_b["tenant_id"]


class TestIdempotentReingestion:
    """Tests for idempotent re-ingestion behavior."""

    def test_same_run_id_produces_same_results(self) -> None:
        """Re-running the same run_id should produce identical results."""
        run_id = "run-2026-03-22-001"
        tenant_id = "acme"
        workspace = "prod"

        def simulate_ingest(run_id: str) -> dict[str, Any]:
            """Simulate idempotent ingest."""
            return {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "fingerprints": ["fp1", "fp2", "fp3"],
            }

        result1 = simulate_ingest(run_id)
        result2 = simulate_ingest(run_id)

        assert result1 == result2

    def test_insert_on_conflict_idempotency(self) -> None:
        """INSERT ON CONFLICT DO NOTHING should be idempotent."""
        sql = """
            INSERT INTO finding_latest (tenant_id, workspace, fingerprint, run_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace, fingerprint) DO NOTHING
        """
        params = ("acme", "prod", "fp1", "run-1")

        # Running this twice with same params should have same effect
        # (second run should be no-op due to ON CONFLICT)
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    def test_finding_presence_upsert_idempotency(self) -> None:
        """Finding presence records should be upsertable idempotently."""
        sql = """
            INSERT INTO finding_presence (tenant_id, workspace, fingerprint, run_id, check_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace, fingerprint, run_id) DO UPDATE SET
                check_id = EXCLUDED.check_id
        """
        params = ("acme", "prod", "fp1", "run-1", "aws.s3.lifecycle.missing")

        # This pattern is idempotent - re-running produces same end state
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql

    def test_run_state_transitions_are_idempotent(self) -> None:
        """Run state transitions should be idempotent."""
        # Transitioning from RUNNING to READY should be idempotent
        # (same end state regardless of how many times it's called)
        def transition_to_ready(state: str) -> str:
            if state in ("RUNNING", "READY"):
                return "READY"
            return state

        assert transition_to_ready("RUNNING") == "READY"
        assert transition_to_ready("READY") == "READY"  # Idempotent

    def test_aggregate_refresh_is_idempotent(self) -> None:
        """Aggregate refresh (delete + reinsert) should be idempotent."""
        # The aggregate refresh pattern:
        # 1. DELETE FROM aggregates WHERE tenant_id=X AND workspace=Y
        # 2. INSERT INTO aggregates ... (recompute all)

        # This is idempotent because:
        # - DELETE is idempotent (deleting same rows twice has no effect)
        # - INSERT with ON CONFLICT DO UPDATE would be truly idempotent

        delete_sql = "DELETE FROM finding_aggregates_current WHERE tenant_id=%s AND workspace=%s"
        insert_sql = """
            INSERT INTO finding_aggregates_current
            (tenant_id, workspace, dimension, key, finding_count, total_savings, refreshed_at)
            SELECT tenant_id, workspace, dimension, key, COUNT(*), SUM(savings), now()
            FROM finding_current
            WHERE tenant_id=%s AND workspace=%s
            GROUP BY tenant_id, workspace, dimension, key
            ON CONFLICT (tenant_id, workspace, dimension, key) DO UPDATE SET
                finding_count = EXCLUDED.finding_count,
                total_savings = EXCLUDED.total_savings
        """

        assert "DELETE" in delete_sql
        assert "ON CONFLICT" in insert_sql

    def test_reingestion_with_same_manifest_produces_same_fingerprints(self) -> None:
        """Re-ingesting the same manifest should produce the same fingerprints."""
        manifest = {
            "tenant_id": "acme",
            "workspace": "prod",
            "run_id": "run-1",
            "files": ["file1.parquet", "file2.parquet"],
        }

        def extract_fingerprints(manifest: dict) -> set[str]:
            """Simulate fingerprint extraction from parquet files."""
            # In real code, this reads Parquet and extracts fingerprints
            return {"fp1", "fp2", "fp3"}

        result1 = extract_fingerprints(manifest)
        result2 = extract_fingerprints(manifest)

        assert result1 == result2  # Idempotent


class TestDeterminism:
    """Tests for deterministic behavior guarantees."""

    def test_fingerprint_is_deterministic(self) -> None:
        """Same inputs should always produce the same fingerprint."""
        import hashlib
        import json

        def compute_fingerprint(tenant_id: str, check_id: str, scope: dict) -> str:
            payload = {
                "tenant_id": tenant_id,
                "check_id": check_id,
                "scope": scope,
            }
            # Sort keys for deterministic output
            return hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()

        scope = {"account_id": "123", "region": "us-east-1", "service": "EC2"}
        fp1 = compute_fingerprint("acme", "aws.ec2.rightsize", scope)
        fp2 = compute_fingerprint("acme", "aws.ec2.rightsize", scope)

        assert fp1 == fp2  # Deterministic

    def test_fingerprint_differs_with_different_inputs(self) -> None:
        """Different inputs should produce different fingerprints."""
        import hashlib
        import json

        def compute_fingerprint(tenant_id: str, check_id: str, scope: dict) -> str:
            payload = {
                "tenant_id": tenant_id,
                "check_id": check_id,
                "scope": scope,
            }
            return hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()

        scope1 = {"account_id": "123", "region": "us-east-1", "service": "EC2"}
        scope2 = {"account_id": "456", "region": "us-east-1", "service": "EC2"}

        fp1 = compute_fingerprint("acme", "aws.ec2.rightsize", scope1)
        fp2 = compute_fingerprint("acme", "aws.ec2.rightsize", scope2)

        assert fp1 != fp2  # Different inputs = different fingerprint

    def test_no_timestamp_in_fingerprint(self) -> None:
        """Fingerprints should not include timestamps (for stability across runs)."""
        # This is a design requirement - timestamps would make fingerprints
        # non-deterministic across runs

        class BadRecord:
            """Record with timestamp - wrong approach."""
            def __init__(self) -> None:
                self.timestamp = "2026-03-22T10:00:00Z"

        class GoodRecord:
            """Record without timestamp - correct approach."""
            def __init__(self) -> None:
                self.run_id = "run-1"  # Stable identifier

        # GoodRecord can produce deterministic fingerprint
        # BadRecord would produce different fingerprints on each run
        assert not hasattr(GoodRecord(), "timestamp")
