defmodule AzteaWeb.TokenTest do
  use ExUnit.Case, async: true

  alias AzteaWeb.Token

  @secret "test-shared-secret"

  defp build_token(user_id, exp, secret \\ @secret) do
    payload = "v1|#{user_id}|#{exp}"
    sig = :crypto.mac(:hmac, :sha256, secret, payload) |> Base.encode16(case: :lower)
    "v1.#{user_id}.#{exp}.#{sig}"
  end

  test "valid token verifies" do
    exp = System.system_time(:second) + 60
    token = build_token("user_a", exp)
    assert {:ok, "user_a"} = Token.verify(token, @secret)
  end

  test "tampered signature rejected" do
    exp = System.system_time(:second) + 60
    token = build_token("user_a", exp)
    tampered = String.replace_trailing(token, String.slice(token, -2..-1), "00")
    assert {:error, :bad_signature} = Token.verify(tampered, @secret)
  end

  test "expired token rejected" do
    exp = System.system_time(:second) - 1
    token = build_token("user_a", exp)
    assert {:error, :expired} = Token.verify(token, @secret)
  end

  test "wrong secret rejected" do
    exp = System.system_time(:second) + 60
    token = build_token("user_a", exp, "another-secret")
    assert {:error, :bad_signature} = Token.verify(token, @secret)
  end

  test "malformed token rejected" do
    assert {:error, :malformed} = Token.verify("not-a-token", @secret)
    assert {:error, :malformed} = Token.verify("v1.only.three", @secret)
  end

  test "missing token / secret rejected" do
    assert {:error, :missing} = Token.verify(nil, @secret)
    assert {:error, :no_secret} = Token.verify("v1.a.123.deadbeef", nil)
    assert {:error, :no_secret} = Token.verify("v1.a.123.deadbeef", "")
  end
end
