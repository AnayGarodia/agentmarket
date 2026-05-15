defmodule AzteaWeb.Router do
  @moduledoc """
  Routes for the realtime event surface.

  /internal/job-events  → fired by the Python app on every state transition.
                          Authenticated with bearer ELIXIR_INTERNAL_SHARED_SECRET.
  /health               → unauthenticated liveness probe for systemd / Caddy.

  The frontend never hits HTTP here — it talks WebSocket via the `/socket`
  scope on AzteaWeb.Endpoint.
  """

  use Phoenix.Router

  pipeline :internal_api do
    plug :accepts, ["json"]
  end

  pipeline :public do
    plug :accepts, ["json"]
  end

  scope "/internal", AzteaWeb do
    pipe_through :internal_api
    post "/job-events", EventController, :publish
  end

  scope "/", AzteaWeb do
    pipe_through :public
    get "/health", HealthController, :show
  end
end
