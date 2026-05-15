import Config

config :aztea, Aztea.Repo,
  show_sensitive_data_on_connection_error: true,
  pool_size: 5

# Dev defaults for the realtime endpoint. Production overrides these in runtime.exs.
config :aztea, AzteaWeb.Endpoint,
  http: [ip: {127, 0, 0, 1}, port: 4000],
  # Long enough that copy-pasted secrets don't surprise a developer.
  secret_key_base: String.duplicate("dev-secret-key-base-aztea-realtime", 2),
  # Token signing secret shared with the Python app. Override via env in prod.
  shared_secret: "dev-shared-secret-not-for-prod",
  debug_errors: false
