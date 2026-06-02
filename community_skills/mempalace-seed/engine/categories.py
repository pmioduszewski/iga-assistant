"""Fixed seed taxonomy. 'abandoned' captures retired tools/brands/decisions so
they can never resurface as current."""

CATEGORIES = [
    "identity", "family", "work_projects", "tools_stack",
    "preferences", "health", "finance", "schedule",
    "commitments", "abandoned",
]

# Curated wings carry signal; the sessions wing is verbatim transcript noise.
SOURCE_WINGS = {
    "user", "people", "projects", "gaia", "wing_gaia", "iga", "wing_iga",
    "vault", "vault-dev-libs", "reference",
}
EXCLUDED_WINGS = {"sessions"}
