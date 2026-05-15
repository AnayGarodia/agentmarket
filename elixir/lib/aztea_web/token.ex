defmodule AzteaWeb.Token do
  @moduledoc """
  HMAC-SHA256 verification for socket tokens issued by the Python app.

  Wire format: `v1.<user_id>.<exp>.<sig_hex>`
    * `v1`        — version tag, locks future format changes behind explicit code paths
    * `user_id`   — the owner_id (UUID) the socket is authorized for
    * `exp`       — Unix epoch seconds when the token stops being valid (5 min from issuance)
    * `sig_hex`   — `hex(hmac_sha256(shared_secret, "v1|" <> user_id <> "|" <> exp))`

  The Python side issues these in `core.job_events.issue_socket_token`. Keeping
  the format dot-separated lets us verify without JSON parsing.

  Why not Phoenix.Token? Phoenix.Token requires the same `secret_key_base`
  on both sides; we want the secret to be the same one used for `Authorization:
  Bearer` on `/internal/job-events` so one env var unblocks the whole surface.
  """

  @version "v1"
  @hash :sha256

  @doc """
  Validate a token. Returns `{:ok, user_id}` or `{:error, reason}`. Compares
  the signature in constant time and refuses tokens whose `exp` is in the past.
  """
  @spec verify(String.t() | nil, String.t() | nil) ::
          {:ok, String.t()} | {:error, :missing | :malformed | :expired | :bad_signature | :no_secret}
  def verify(token, shared_secret) when is_binary(token) and is_binary(shared_secret) and byte_size(shared_secret) > 0 do
    with [@version, user_id, exp_str, signature_hex] <- String.split(token, "."),
         {exp, ""} <- Integer.parse(exp_str),
         :ok <- check_expiry(exp),
         payload = signed_payload(user_id, exp_str),
         expected = :crypto.mac(:hmac, @hash, shared_secret, payload) |> Base.encode16(case: :lower),
         true <- constant_time_eq(signature_hex, expected) do
      {:ok, user_id}
    else
      false -> {:error, :bad_signature}
      {:error, _} = err -> err
      _ -> {:error, :malformed}
    end
  end

  def verify(nil, _), do: {:error, :missing}
  def verify(_, nil), do: {:error, :no_secret}
  def verify(_, ""), do: {:error, :no_secret}
  def verify(_, _), do: {:error, :malformed}

  @doc """
  Constant-time equality check on two strings. Used both for token signature
  verification and bearer-header comparison so a timing attack can't recover
  either secret byte-by-byte.
  """
  @spec constant_time_eq(binary(), binary()) :: boolean()
  def constant_time_eq(a, b) when is_binary(a) and is_binary(b) do
    Plug.Crypto.secure_compare(a, b)
  end

  def constant_time_eq(_, _), do: false

  defp signed_payload(user_id, exp_str), do: @version <> "|" <> user_id <> "|" <> exp_str

  defp check_expiry(exp) do
    if exp >= System.system_time(:second), do: :ok, else: {:error, :expired}
  end
end
