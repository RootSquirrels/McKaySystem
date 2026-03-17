"""Unit tests for graph resource key resolution helpers."""

from apps.flask_api.graph_context import graph_resource_key_from_payload


def test_graph_resource_key_prefers_primary_resource_id_over_dimension_resource_arn() -> None:
    """Function-level identifiers should win over incidental dimension ARNs."""
    payload = {
        "dimensions": {
            "function_name": "Engie-AWS-IAM-Events-tf-test",
            "resource_arn": (
                "arn:aws:logs:eu-west-1:288276694458:"
                "log-group:/aws/lambda/Engie-AWS-IAM-Events-tf-test:*"
            ),
        }
    }

    resource_key = graph_resource_key_from_payload(
        payload,
        account_id="288276694458",
        region="eu-west-1",
        service="lambda",
    )

    assert resource_key == (
        "aws:288276694458:eu-west-1:lambda:function:Engie-AWS-IAM-Events-tf-test"
    )


def test_graph_resource_key_uses_dimension_arn_when_no_primary_resource_id_exists() -> None:
    """Dimension ARNs should still be used when they are the only stable identity."""
    payload = {
        "dimensions": {
            "load_balancer_arn": (
                "arn:aws:elasticloadbalancing:eu-west-1:288276694458:"
                "loadbalancer/app/demo/50dc6c495c0c9188"
            )
        }
    }

    resource_key = graph_resource_key_from_payload(
        payload,
        account_id="288276694458",
        region="eu-west-1",
        service="elbv2",
    )

    assert resource_key == (
        "aws:288276694458:eu-west-1:elbv2:load_balancer:"
        "arn:aws:elasticloadbalancing:eu-west-1:288276694458:"
        "loadbalancer/app/demo/50dc6c495c0c9188"
    )
