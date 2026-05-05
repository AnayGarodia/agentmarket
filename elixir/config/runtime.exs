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
end
