defmodule AzteaWeb.Endpoint do
  @moduledoc """
  Phoenix Endpoint for the realtime job-event surface.

  Two roles:
    * inbound HTTP `POST /internal/job-events` from the Python app (loopback only,
      bearer-authenticated with ELIXIR_INTERNAL_SHARED_SECRET)
    * outbound WebSocket at `/socket` consumed by the React frontend so jobs
      list updates land in <1s instead of waiting on the 5-second poll.

  Only loopback ports are exposed in production. Caddy is responsible for
  terminating TLS and upgrading the `/elixir/socket` path to this endpoint.
  """

  use Phoenix.Endpoint, otp_app: :aztea

  # WebSocket transport. Long-poll fallback is enabled so corporate networks
  # blocking WS still receive realtime events (just with a small latency hit).
  socket "/socket", AzteaWeb.UserSocket,
    websocket: [timeout: 45_000, connect_info: [:peer_data, :x_headers]],
    longpoll: false

  plug Plug.RequestId
  plug Plug.Telemetry, event_prefix: [:aztea_web, :endpoint]

  plug Plug.Parsers,
    parsers: [:urlencoded, :json],
    pass: ["*/*"],
    json_decoder: Phoenix.json_library()

  plug Plug.MethodOverride
  plug Plug.Head

  plug AzteaWeb.Router
end
