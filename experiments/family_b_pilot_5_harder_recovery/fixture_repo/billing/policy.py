"""Late-fee policy configuration."""

ACTIVE_POLICY = {
    "US": {"grace_days": 10, "fee_rate": 0.015},
    "EU": {"grace_days": 14, "fee_rate": 0.010},
    "UK": {"grace_days": 14, "fee_rate": 0.010},
}

# Superseded by ACTIVE_POLICY after the 2024 collections policy update;
# nothing in the current late-fee pipeline should read this.
DEPRECATED_POLICY = {
    "US": {"grace_days": 30, "fee_rate": 0.020},
    "EU": {"grace_days": 30, "fee_rate": 0.015},
    "UK": {"grace_days": 30, "fee_rate": 0.015},
}
