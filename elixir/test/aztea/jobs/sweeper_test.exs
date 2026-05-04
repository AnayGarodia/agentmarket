defmodule Aztea.Jobs.SweeperTest do
  use ExUnit.Case, async: false

  @moduletag :integration

  test "sweeper starts without crashing" do
    # Sweeper is already started by the application supervisor.
    # Just verify the process is alive.
    pid = Process.whereis(Aztea.Jobs.Sweeper)
    assert is_pid(pid)
    assert Process.alive?(pid)
  end
end
