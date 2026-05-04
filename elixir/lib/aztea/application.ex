defmodule Aztea.Application do
  @moduledoc """
  OTP Application — starts the supervision tree for the Aztea job lifecycle service.

  Process hierarchy:
    Aztea.Application
      └── Aztea.Repo                    (Ecto Postgres connection pool)
      └── Phoenix.PubSub                (job event broadcast)
      └── Aztea.Jobs.Supervisor         (DynamicSupervisor — one child per job)
      └── Aztea.Jobs.Sweeper            (periodic lease-expiry checker)
  """

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      Aztea.Repo,
      {Phoenix.PubSub, name: Aztea.PubSub},
      {Registry, keys: :unique, name: Aztea.Jobs.Registry},
      {DynamicSupervisor, strategy: :one_for_one, name: Aztea.Jobs.Supervisor},
      Aztea.Jobs.Sweeper
    ]

    opts = [strategy: :one_for_one, name: Aztea.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
