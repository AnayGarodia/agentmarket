defmodule Aztea.Jobs.Sweeper do
  @moduledoc """
  Periodic GenServer that scans for expired leases and timed-out jobs.

  Runs every `@sweep_interval_ms` (default 5s). For each expired job it:
    1. Resets lease-expired running jobs → pending (so workers can re-claim)
    2. Spawns a JobServer for any active job that doesn't have one yet
       (recovery after a node restart)
    3. Fails jobs that have exceeded their timeout_seconds

  This mirrors the Python sweeper in server/application_parts/part_006.py but
  runs as a native OTP process for better fault isolation and observability.
  """

  use GenServer

  require Logger
  import Ecto.Query

  alias Aztea.Repo
  alias Aztea.Jobs.Schema, as: Job
  alias Aztea.Jobs.JobServer

  @sweep_interval_ms 5_000
  @active_statuses ~w[pending running awaiting_clarification]

  def start_link(_opts), do: GenServer.start_link(__MODULE__, [], name: __MODULE__)

  @impl true
  def init(_) do
    schedule_sweep()
    {:ok, %{sweep_count: 0}}
  end

  @impl true
  def handle_info(:sweep, state) do
    sweep()
    schedule_sweep()
    {:noreply, %{state | sweep_count: state.sweep_count + 1}}
  end

  # ---------------------------------------------------------------------------

  defp sweep do
    now = DateTime.utc_now() |> DateTime.to_iso8601()

    # 1. Expire stale running leases.
    {expired_count, expired_jobs} =
      Repo.update_all(
        from(j in Job,
          where:
            j.status == "running" and
              not is_nil(j.lease_expires_at) and
              j.lease_expires_at < ^now,
          select: j.job_id
        ),
        set: [status: "pending", lease_expires_at: nil, updated_at: now]
      )

    if expired_count > 0 do
      Logger.info("sweeper.expired_leases", count: expired_count, job_ids: expired_jobs)
    end

    # 2. Fail jobs that have exceeded timeout_seconds.
    timeout_cutoff = DateTime.utc_now() |> DateTime.to_iso8601()

    {timed_out_count, _} =
      Repo.update_all(
        from(j in Job,
          where:
            j.status in ^["pending", "running"] and
              not is_nil(j.timeout_seconds) and
              j.timeout_seconds > 0 and
              datetime_add(j.created_at, j.timeout_seconds, "second") < ^timeout_cutoff
        ),
        set: [
          status: "failed",
          error: "timeout",
          updated_at: now
        ]
      )

    if timed_out_count > 0 do
      Logger.info("sweeper.timed_out", count: timed_out_count)
    end

    # 3. Ensure a JobServer exists for every active job (node recovery path).
    active_jobs =
      Repo.all(
        from(j in Job,
          where: j.status in ^@active_statuses,
          select: j.job_id
        )
      )

    Enum.each(active_jobs, fn job_id ->
      case JobServer.start_or_find(job_id) do
        {:ok, _pid} -> :ok
        {:error, reason} -> Logger.warning("sweeper.start_failed", job_id: job_id, reason: inspect(reason))
      end
    end)
  end

  defp schedule_sweep do
    Process.send_after(self(), :sweep, @sweep_interval_ms)
  end
end
