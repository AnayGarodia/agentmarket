import Config

# DATABASE_URL is required in production.
config :aztea, Aztea.Repo,
  ssl: true,
  pool_size: String.to_integer(System.get_env("ELIXIR_DB_POOL_SIZE") || "10")
