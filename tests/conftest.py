"""Pytest bootstrap.

Every ``src/<fn>/app.py`` resolves ``os.environ[...]`` and constructs boto3
clients/resources at *module top level*, so those env vars must exist before
any app module is imported. Pytest imports this conftest before collecting the
test modules, which makes it the right place to set them.

No AWS calls happen here — boto3 client/resource construction is offline; we
only supply a region so botocore doesn't raise ``NoRegionError`` on import, and
dummy credentials so it never probes the instance metadata service.
"""

import os

# Region + dummy credentials (offline; never used to make a real call).
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

# Required-at-import env vars across the Lambda modules (values are placeholders).
os.environ["HOT_TABLE"] = "test-hot"
os.environ["SSM_PREFIX"] = "/test/line"
os.environ["CURATED_BUCKET"] = "test-curated"
os.environ["MARTS_BUCKET"] = "test-marts"
os.environ["BEDROCK_MODEL_ID"] = "test-model"
os.environ["INGEST_QUEUE_URL"] = "https://sqs.test.local/queue"

# A *weekday* market holiday so trading-day tests can prove the holiday branch
# is independent of the weekend branch. 2026-06-19 is 端午 (Dragon Boat), a
# Friday that was closed — see the project's MARKET_HOLIDAYS handling.
os.environ["MARKET_HOLIDAYS"] = "2026-06-19"
