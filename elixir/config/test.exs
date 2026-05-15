import Config

config :aztea, Aztea.Repo,
  url: System.get_env("TEST_DATABASE_URL") || "postgres://localhost/aztea_test",
  pool: Ecto.Adapters.SQL.Sandbox,
  pool_size: 5

# Bind the endpoint to an ephemeral port — tests call controllers directly,
# the HTTP listener stays off so we never race with another test run.
config :aztea, AzteaWeb.Endpoint,
  http: [ip: {127, 0, 0, 1}, port: 0],
  server: false,
  secret_key_base: String.duplicate("test-secret-key-base-aztea-realtime", 2),
  shared_secret: "test-shared-secret"
