import Config

# Compile-time prod defaults only. DATABASE_URL and ssl are set in runtime.exs
# so the release binary can be built without a live database connection.
config :aztea, Aztea.Repo,
  pool_size: 5
