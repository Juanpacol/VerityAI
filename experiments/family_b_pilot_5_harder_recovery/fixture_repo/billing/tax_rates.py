"""Tax configuration."""

REGION_RATES = {
    "US": 0.07,
    "EU": 0.20,
    "UK": 0.20,
}

# Superseded by REGION_RATES after the 2024 tax reform; nothing in the
# current tax pipeline should read this.
LEGACY_REGION_RATES = {
    "US": 0.05,
    "EU": 0.19,
    "UK": 0.20,
}
