defmodule Aztea.MixProject do
  use Mix.Project

  def project do
    [
      app: :aztea,
      version: "0.1.0",
      elixir: "~> 1.16",
      start_permanent: Mix.env() == :prod,
      deps: deps(),
      aliases: aliases()
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {Aztea.Application, []}
    ]
  end

  defp deps do
    [
      # Postgres via Ecto — shares the same DB as the Python server
      {:ecto_sql, "~> 3.11"},
      {:postgrex, "~> 0.17"},
      # Real-time job events (optional Phoenix Channels later)
      {:phoenix_pubsub, "~> 2.1"},
      # JSON for job payloads
      {:jason, "~> 1.4"},
      # Test helpers
      {:ex_machina, "~> 2.8", only: :test}
    ]
  end

  defp aliases do
    [
      "ecto.setup": ["ecto.create", "ecto.migrate"],
      "ecto.reset": ["ecto.drop", "ecto.setup"],
      test: ["ecto.create --quiet", "ecto.migrate --quiet", "test"]
    ]
  end
end
