defmodule Aztea.Repo do
  use Ecto.Repo,
    otp_app: :aztea,
    adapter: Ecto.Adapters.Postgres
end
