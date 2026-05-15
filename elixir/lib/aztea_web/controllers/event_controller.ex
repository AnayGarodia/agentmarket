defmodule AzteaWeb.EventController do
  @moduledoc """
  Receives job state transitions from the Python app over loopback HTTP and
  fans them out on `Phoenix.PubSub` topic `user:<user_id>`. Subscribed
  `JobChannel` processes push the event to their connected client.

  Wire format (JSON body):
      {
        "user_id":    "owner_id from the job row",
        "job_id":     "the affected job",
        "event_type": "job.created" | "job.claimed" | "job.complete" | ...
        "payload":    { … free-form, mirrored verbatim … }
      }

  Authentication: `Authorization: Bearer <ELIXIR_INTERNAL_SHARED_SECRET>`.
  The secret is the same one the Python `core.job_events` module signs socket
  tokens with — sharing one secret keeps the surface minimal.

  Failure modes are intentionally narrow:
    * missing/wrong bearer → 401
    * missing required JSON fields → 400
    * everything else → 204 with PubSub broadcast fire-and-forget

  Never raises a 500. The Python caller is best-effort and will log a
  warning; we don't want a malformed event to cascade into upstream errors.
  """

  use Phoenix.Controller, formats: [:json]

  require Logger

  alias AzteaWeb.Token

  @required_fields ~w(user_id job_id event_type)

  def publish(conn, params) do
    with :ok <- authorize(conn),
         {:ok, normalized} <- validate(params) do
      broadcast(normalized)

      conn
      |> send_resp(204, "")
      |> halt()
    else
      {:error, :unauthorized} ->
        conn |> put_status(401) |> json(%{error: "unauthorized"}) |> halt()

      {:error, :bad_request, reason} ->
        conn
        |> put_status(400)
        |> json(%{error: "bad_request", detail: reason})
        |> halt()
    end
  end

  defp authorize(conn) do
    expected = Application.get_env(:aztea, AzteaWeb.Endpoint)[:shared_secret]

    case get_req_header(conn, "authorization") do
      ["Bearer " <> given] when is_binary(expected) and byte_size(expected) > 0 ->
        if Token.constant_time_eq(given, expected) do
          :ok
        else
          {:error, :unauthorized}
        end

      _ ->
        {:error, :unauthorized}
    end
  end

  defp validate(params) when is_map(params) do
    missing = Enum.filter(@required_fields, fn key -> blank?(Map.get(params, key)) end)

    if missing == [] do
      {:ok,
       %{
         user_id: to_string(Map.fetch!(params, "user_id")),
         job_id: to_string(Map.fetch!(params, "job_id")),
         event_type: to_string(Map.fetch!(params, "event_type")),
         payload: Map.get(params, "payload") || %{}
       }}
    else
      {:error, :bad_request, "missing fields: #{Enum.join(missing, ", ")}"}
    end
  end

  defp validate(_), do: {:error, :bad_request, "body must be a JSON object"}

  defp blank?(nil), do: true
  defp blank?(""), do: true
  defp blank?(v) when is_binary(v), do: String.trim(v) == ""
  defp blank?(_), do: false

  defp broadcast(%{user_id: user_id} = event) do
    topic = "user:" <> user_id
    message = Map.delete(event, :user_id)

    Phoenix.PubSub.broadcast(Aztea.PubSub, topic, {:job_event, message})
  end
end
