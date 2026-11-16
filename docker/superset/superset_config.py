from pathlib import Path


def secret(name):
    return (Path('/run/secrets')/name).read_text().strip()


SECRET_KEY=secret('superset_secret')
SQLALCHEMY_DATABASE_URI='postgresql+psycopg2://superset_meta:'+secret('metadata_password')+'@database:5432/superset_meta'
SQLALCHEMY_ENGINE_OPTIONS={'pool_size':5,'max_overflow':5}
FEATURE_FLAGS={'EMBEDDED_SUPERSET':True,'DASHBOARD_CROSS_FILTERS':True,'ENABLE_TEMPLATE_PROCESSING':False}
WTF_CSRF_ENABLED=True
SESSION_COOKIE_NAME='datapilot_superset'
SESSION_COOKIE_SAMESITE='Lax'
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SECURE=False
ENABLE_PROXY_FIX=False
TALISMAN_ENABLED=True
TALISMAN_CONFIG={'force_https':False,'session_cookie_secure':False,'content_security_policy':None,'frame_options':None}
GUEST_ROLE_NAME='Gamma'
GUEST_TOKEN_JWT_SECRET=secret('guest_secret')
GUEST_TOKEN_JWT_EXP_SECONDS=300
MCP_AUTH_ENABLED=True
MCP_JWT_ALGORITHM='HS256'
MCP_JWT_SECRET=secret('mcp_secret')
MCP_JWT_ISSUER='datapilot-local'
MCP_JWT_AUDIENCE='superset-mcp'
MCP_RBAC_ENABLED=True
MCP_TOOL_SEARCH_CONFIG={'enabled':False}
MCP_DEV_USERNAME='datapilot'
SUPERSET_WEBSERVER_ADDRESS='http://localhost:8088'
WEBDRIVER_BASEURL='http://web:8088/'
WEBDRIVER_BASEURL_USER_FRIENDLY='http://localhost:8088/'
SQL_MAX_ROW=10000
ROW_LIMIT=10000
SQLLAB_TIMEOUT=30
SUPERSET_WEBSERVER_TIMEOUT=120
ENABLE_CORS=False
