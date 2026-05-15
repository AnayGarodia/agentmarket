defmodule AzteaWeb.EventControllerTest do
  use ExUnit.Case, async: false

  @endpoint AzteaWeb.Endpoint
  @secret "test-shared-secret"
  @topic "user:user_abc"

  setup do
    # Tests subscribe to the user topic so we can assert the broadcast arrives.
    Phoenix.PubSub.subscribe(Aztea.PubSub, @topic)
    on_exit(fn -> Phoenix.PubSub.unsubscribe(Aztea.PubSub, @topic) end)
    :ok
  end

  defp post_event(body, headers \\ []) do
    conn =
      Phoenix.ConnTest.build_conn(:post, "/internal/job-events", body)
      |> Plug.Conn.put_req_header("content-type", "application/json")

    Enum.reduce(headers, conn, fn {k, v}, acc -> Plug.Conn.put_req_header(acc, k, v) end)
    |> AzteaWeb.Router.call(AzteaWeb.Router.init([]))
  end

  defp event_body do
    %{
      "user_id" => "user_abc",
      "job_id" => "job_42",
      "event_type" => "job.complete",
      "payload" => %{"status" => "complete"}
    }
  end

  test "POST without bearer → 401" do
    conn = post_event(event_body())
    assert conn.status == 401
    refute_received {:job_event, _}
  end

  test "POST with wrong bearer → 401" do
    conn = post_event(event_body(), [{"authorization", "Bearer wrong-secret"}])
    assert conn.status == 401
    refute_received {:job_event, _}
  end

  test "POST with correct bearer → 204 + PubSub broadcast" do
    conn = post_event(event_body(), [{"authorization", "Bearer " <> @secret}])
    assert conn.status == 204

    assert_receive {:job_event,
                    %{
                      job_id: "job_42",
                      event_type: "job.complete",
                      payload: %{"status" => "complete"}
                    }},
                   500
  end

  test "POST with missing user_id → 400" do
    body = event_body() |> Map.delete("user_id")
    conn = post_event(body, [{"authorization", "Bearer " <> @secret}])
    assert conn.status == 400
    refute_received {:job_event, _}
  end
end
