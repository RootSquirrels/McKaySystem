"""Registered Flask API blueprints by domain."""
from apps.flask_api.blueprints.api_keys import api_keys_bp
from apps.flask_api.blueprints.auth import auth_bp
from apps.flask_api.blueprints.facets import facets_bp
from apps.flask_api.blueprints.findings import findings_bp
from apps.flask_api.blueprints.groups import groups_bp
from apps.flask_api.blueprints.health import health_bp
from apps.flask_api.blueprints.kpis import kpis_bp
from apps.flask_api.blueprints.lifecycle import lifecycle_bp
from apps.flask_api.blueprints.recommendations import recommendations_bp
from apps.flask_api.blueprints.remediations import remediations_bp
from apps.flask_api.blueprints.runs import runs_bp
from apps.flask_api.blueprints.sla_policies import sla_policies_bp
from apps.flask_api.blueprints.tenant_admin import tenant_admin_bp
from apps.flask_api.blueprints.teams import teams_bp
from apps.flask_api.blueprints.users import users_bp

__all__ = [
    "health_bp",
    "auth_bp",
    "users_bp",
    "api_keys_bp",
    "kpis_bp",
    "runs_bp",
    "findings_bp",
    "recommendations_bp",
    "teams_bp",
    "tenant_admin_bp",
    "sla_policies_bp",
    "lifecycle_bp",
    "remediations_bp",
    "groups_bp",
    "facets_bp",
]
