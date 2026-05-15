import Config

config :aztea, Aztea.Repo,
  # Reads the same DATABASE_URL used by the Python server.
  # Falls back to a local dev DB when DATABASE_URL is not set.
  url: System.get_env("DATABASE_URL") || "postgres://localhost/aztea_dev",
  pool_size: String.to_integer(System.get_env("ELIXIR_DB_POOL_SIZE") || "5"),
  # aztea uses TEXT for timestamps throughout — disable Ecto's default
  # Naive/UTC datetime casting so we can pass ISO strings through unchanged.
  prepare: :unnamed

config :aztea, ecto_repos: [Aztea.Repo]

# Phoenix endpoint for the realtime job-event surface. Token signing secrets
# and the HTTP port land in runtime.exs so the release can boot without them
# at build time.
config :aztea, AzteaWeb.Endpoint,
  url: [host: "localhost"],
  render_errors: [formats: [json: AzteaWeb.ErrorJSON], layout: false],
  pubsub_server: Aztea.PubSub,
  server: true

config :phoenix, :json_library, Jason

config :logger, :console,
  format: "$time $metadata[$level] $message\n",
  metadata: [:request_id, :job_id]

import_config "#{config_env()}.exs"
