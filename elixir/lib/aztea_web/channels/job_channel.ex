defmodule AzteaWeb.JobChannel do
  @moduledoc """
  Phoenix Channel for the per-user job event feed.

  Topic shape: `user:<user_id>`. A client may only join the topic that
  matches the user_id it authenticated with on UserSocket — joining any
  other user_id is rejected so a stolen-but-valid token cannot exfiltrate
  another user's job events.

  Inbound: no client→server messages are accepted; this channel is read-only.
  Outbound: every `{:job_event, %{...}}` PubSub message landing on the
  subscribed topic is forwarded as a `"job_event"` push frame with the same
  payload the Python controller posted.
  """

  use Phoenix.Channel

  @impl true
  def join("user:" <> requested_user_id, _params, socket) do
    case socket.assigns[:user_id] do
      ^requested_user_id ->
        Phoenix.PubSub.subscribe(Aztea.PubSub, "user:" <> requested_user_id)
        {:ok, socket}

      _ ->
        {:error, %{reason: "forbidden"}}
    end
  end

  def join(_other_topic, _params, _socket), do: {:error, %{reason: "unknown_topic"}}

  @impl true
  def handle_info({:job_event, payload}, socket) do
    push(socket, "job_event", payload)
    {:noreply, socket}
  end

  # Ignore unrelated messages (Phoenix may forward link/exit signals).
  def handle_info(_other, socket), do: {:noreply, socket}
end
