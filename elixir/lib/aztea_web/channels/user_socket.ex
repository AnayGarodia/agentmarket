defmodule AzteaWeb.UserSocket do
  @moduledoc """
  Connection-time auth for the realtime socket.

  Clients connect with `wss://host/elixir/socket/websocket?token=<v1...>`.
  The token is HMAC-signed by the Python app (`core.job_events.issue_socket_token`)
  with the same secret used for the inbound bearer on `/internal/job-events`.

  On successful verification the resolved `user_id` is stashed in
  `socket.assigns.user_id` so channel `join/3` can refuse cross-user joins.
  """

  use Phoenix.Socket

  channel "user:*", AzteaWeb.JobChannel

  @impl true
  def connect(%{"token" => token}, socket, _connect_info) do
    shared_secret = Application.get_env(:aztea, AzteaWeb.Endpoint)[:shared_secret]

    case AzteaWeb.Token.verify(token, shared_secret) do
      {:ok, user_id} -> {:ok, assign(socket, :user_id, user_id)}
      {:error, _reason} -> :error
    end
  end

  def connect(_params, _socket, _connect_info), do: :error

  @impl true
  def id(%{assigns: %{user_id: user_id}}), do: "users_socket:" <> user_id
  def id(_), do: nil
end
