defmodule AzteaWeb.ErrorJSON do
  @moduledoc """
  Phoenix renders this module for unhandled errors. Keep the surface small:
  the realtime endpoint is internal; no UX rendering of error pages.
  """

  def render(template, _assigns) do
    %{error: Phoenix.Controller.status_message_from_template(template)}
  end
end
