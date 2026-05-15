defmodule Aztea.Application do
  @moduledoc """
  OTP Application — starts the supervision tree for the Aztea Elixir service.

  Process hierarchy:
    Aztea.Application
      └── Aztea.Repo                    (Ecto Postgres connection pool)
      └── Phoenix.PubSub                (job event broadcast)
      └── Aztea.Jobs.Supervisor         (DynamicSupervisor — one child per job)
      └── Aztea.Jobs.Sweeper            (periodic lease-expiry checker)
      └── AzteaWeb.Endpoint             (HTTP + WebSocket for realtime fan-out)
  """

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      Aztea.Repo,
      {Phoenix.PubSub, name: Aztea.PubSub},
      {Registry, keys: :unique, name: Aztea.Jobs.Registry},
      {DynamicSupervisor, strategy: :one_for_one, name: Aztea.Jobs.Supervisor},
      Aztea.Jobs.Sweeper,
      AzteaWeb.Endpoint
    ]

    opts = [strategy: :one_for_one, name: Aztea.Supervisor]
    Supervisor.start_link(children, opts)
  end

  # Phoenix calls this on each config change so the endpoint can pick up
  # rotated secrets without restarting the whole VM.
  def config_change(changed, _new, removed) do
    AzteaWeb.Endpoint.config_change(changed, removed)
    :ok
  end
end
