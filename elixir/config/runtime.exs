import Config

# Runtime config — evaluated when the release boots, not at compile time.
# DATABASE_URL is required in production; falls back to dev default otherwise.

if config_env() == :prod do
  database_url =
    System.get_env("DATABASE_URL") ||
      raise "DATABASE_URL environment variable is required in production"

  config :aztea, Aztea.Repo,
    url: database_url,
    # Localhost Postgres does not need SSL.
    ssl: false,
    pool_size: String.to_integer(System.get_env("ELIXIR_DB_POOL_SIZE") || "5")

  http_port =
    System.get_env("ELIXIR_HTTP_PORT", "4000")
    |> String.to_integer()

  # secret_key_base is internal to Phoenix; generated once per host. Prefer
  # ELIXIR_SECRET_KEY_BASE if set, otherwise derive from the shared secret
  # so a single env var unblocks deploys.
  shared_secret =
    System.get_env("ELIXIR_INTERNAL_SHARED_SECRET") ||
      raise """
      ELIXIR_INTERNAL_SHARED_SECRET environment variable is required in production.
      Generate with: openssl rand -hex 32
      """

  secret_key_base =
    System.get_env("ELIXIR_SECRET_KEY_BASE") ||
      :crypto.hash(:sha512, "aztea-endpoint:" <> shared_secret) |> Base.encode64()

  config :aztea, AzteaWeb.Endpoint,
    http: [ip: {127, 0, 0, 1}, port: http_port],
    secret_key_base: secret_key_base,
    shared_secret: shared_secret,
    server: true
end
