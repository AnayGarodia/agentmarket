defmodule AzteaWeb.HealthController do
  @moduledoc """
  Unauthenticated liveness probe. Returns 200 if the endpoint is up. Used
  by systemd and Caddy to decide whether the realtime path is reachable
  before flipping AZTEA_ELIXIR_EVENTS on the Python side.
  """

  use Phoenix.Controller, formats: [:json]

  def show(conn, _params) do
    json(conn, %{status: "ok", service: "aztea-elixir-web"})
  end
end
