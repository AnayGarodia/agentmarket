import Config

config :aztea, Aztea.Repo,
  url: System.get_env("TEST_DATABASE_URL") || "postgres://localhost/aztea_test",
  pool: Ecto.Adapters.SQL.Sandbox,
  pool_size: 5
