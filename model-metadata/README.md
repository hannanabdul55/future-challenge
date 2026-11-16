# model-metadata

One YAML per entry, named `<team_abbr>-<model_abbr>.yml`. Required fields:

| Field | Meaning |
|---|---|
| `model_id` | `<team_abbr>-<model_abbr>`; matches the `model-output/` directory |
| `primary` | exactly one `true` per team per round (DESIGN.md §8.8) |
| `tier` | `S` sealed · `A` attested · `B` self-reported · `H` human/hybrid (§7.5) |
| `model_identity` | `{type: weights, sha256, inference_image}` · `{type: container, oci_digest, entrypoint, network_policy}` · `{type: api, provider, model_id, fingerprint}` |
| `public_key_pem` | Ed25519 public key used to sign commit requests (§6.2) |
| `methods`, `data_inputs` | free text; published at reveal |

Tier claims above what the identity record supports are rejected in CI (tier S requires a container digest; API identities are capped at tier A).

## Operator entries

The `house-` prefix (`team_abbr` `house`, e.g. `house-qwen2.5-14b-q4`) is reserved for entries the hub operator runs itself. They are Tier S: frozen, run and published like any entry, on the same packet and code. They are never ranked: the board lists them in a separate "Operator entries (house) — not ranked" section, and they are left out of the ranked relative-skill table and the hub-ensemble baseline, because pairwise relative skill depends on the set of models compared. Registration CI rejects a pull request from anyone other than the repository owner, a member or a collaborator that adds or edits `model-metadata/house-*.yml`.
