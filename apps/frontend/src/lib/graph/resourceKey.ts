"use client";

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function firstNonEmptyText(...values: unknown[]): string | null {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) {
      return text;
    }
  }
  return null;
}

function inferResourceKind(resourceId: string | null, resourceArn: string | null): {
  resourceType: string;
  service: string;
} {
  const arnText = String(resourceArn ?? "").trim().toLowerCase();
  const resourceText = String(resourceId ?? "").trim();

  if (arnText) {
    if (arnText.includes(":elasticloadbalancing:")) {
      return { resourceType: "load_balancer", service: "elbv2" };
    }
    if (arnText.includes(":lambda:")) {
      return { resourceType: "function", service: "lambda" };
    }
    if (arnText.includes(":rds:")) {
      return { resourceType: "db_instance", service: "rds" };
    }
    if (arnText.includes(":s3:::")) {
      return { resourceType: "bucket", service: "s3" };
    }
  }

  if (resourceText.startsWith("i-")) {
    return { resourceType: "instance", service: "ec2" };
  }
  if (resourceText.startsWith("vol-")) {
    return { resourceType: "volume", service: "ec2" };
  }
  if (resourceText.startsWith("vpc-")) {
    return { resourceType: "vpc", service: "vpc" };
  }
  if (resourceText.startsWith("subnet-")) {
    return { resourceType: "subnet", service: "vpc" };
  }
  if (resourceText.startsWith("nat-")) {
    return { resourceType: "nat_gateway", service: "vpc" };
  }
  if (resourceText.startsWith("sg-")) {
    return { resourceType: "security_group", service: "ec2" };
  }
  if (resourceText.startsWith("rtb-")) {
    return { resourceType: "route_table", service: "vpc" };
  }
  if (resourceText.startsWith("tg-")) {
    return { resourceType: "target_group", service: "elbv2" };
  }
  return { resourceType: "resource", service: "unknown" };
}

function normalizeResourceType(resourceType: string | null): string {
  const normalized = String(resourceType ?? "").trim().toLowerCase().replace(/-/g, "_") || "resource";
  const aliases: Record<string, string> = {
    ebs_volume: "volume",
    ebs_snapshot: "snapshot",
    s3_bucket: "bucket",
    ec2_instance: "instance",
    security_group: "security_group",
    nat_gateway: "nat_gateway",
    db_instance: "db_instance",
  };
  return aliases[normalized] ?? normalized;
}

function normalizeService(service: string | null, resourceType: string | null): string {
  const normalized = String(service ?? "").trim().toLowerCase().replace(/\s+/g, "") || "unknown";
  const normalizedType = normalizeResourceType(resourceType);
  if (["vpc", "subnet", "nat_gateway", "route_table"].includes(normalizedType)) {
    return "vpc";
  }
  if (["instance", "volume", "snapshot", "security_group"].includes(normalizedType)) {
    return "ec2";
  }
  if (normalizedType === "bucket") {
    return "s3";
  }
  if (["load_balancer", "target_group"].includes(normalizedType)) {
    return "elbv2";
  }
  if (normalizedType === "db_instance") {
    return "rds";
  }
  if (normalizedType === "function") {
    return "lambda";
  }

  const aliases: Record<string, string> = {
    amazonec2: "ec2",
    ec2: "ec2",
    vpc: "vpc",
    amazons3: "s3",
    s3: "s3",
    elasticloadbalancingv2: "elbv2",
    elbv2: "elbv2",
    rds: "rds",
    awslambda: "lambda",
    lambda: "lambda",
  };
  return aliases[normalized] ?? normalized;
}

export function graphResourceKeyFromPayload(
  payload: Record<string, unknown> | null,
  fallback: {
    accountId?: string | null;
    region?: string | null;
    service?: string | null;
  } = {},
): string | null {
  if (!payload) {
    return null;
  }

  const scope = asRecord(payload.scope);
  const dimensions = asRecord(payload.dimensions);

  const accountId = firstNonEmptyText(scope?.account_id, payload.account_id, fallback.accountId);
  if (!accountId) {
    return null;
  }

  const region = firstNonEmptyText(scope?.region, payload.region, fallback.region) ?? "";
  const primaryResourceArn = firstNonEmptyText(
    scope?.resource_arn,
    payload.resource_arn,
  );
  const resourceId = firstNonEmptyText(
    scope?.resource_id,
    payload.resource_id,
    dimensions?.resource_id,
    dimensions?.instance_id,
    dimensions?.bucket,
    dimensions?.bucket_name,
    dimensions?.db_instance_identifier,
    dimensions?.db_cluster_identifier,
    dimensions?.nat_gateway_id,
    dimensions?.function_name,
    dimensions?.load_balancer_name,
    dimensions?.volume_id,
    dimensions?.snapshot_id,
    dimensions?.file_system_id,
    dimensions?.vault_name,
    dimensions?.plan_name,
    dimensions?.cluster_name,
    dimensions?.service_name,
  );
  const secondaryResourceArn = firstNonEmptyText(
    dimensions?.load_balancer_arn,
    dimensions?.resource_arn,
  );
  const nativeId = primaryResourceArn ?? resourceId ?? secondaryResourceArn;
  if (!nativeId) {
    return null;
  }

  const inferred = inferResourceKind(resourceId, primaryResourceArn ?? secondaryResourceArn);
  const resourceType = normalizeResourceType(
    firstNonEmptyText(scope?.resource_type, payload.resource_type, inferred.resourceType),
  );
  const service = normalizeService(
    firstNonEmptyText(scope?.service, payload.service, fallback.service, inferred.service),
    resourceType,
  );

  return `aws:${accountId}:${region}:${service}:${resourceType}:${nativeId}`;
}
