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
