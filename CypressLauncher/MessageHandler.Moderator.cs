#nullable enable
using System;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace CypressLauncher;

public partial class MessageHandler
{
	private string? m_modToken;
	private string? m_modUsername;

	// global ban check toggle - servers opt in via host settings
	private bool m_useGlobalBanDB = true;

	private void OnModRegister(JObject msg)
	{
		string username = ((string?)msg["username"] ?? "").Trim().ToLower();
		string password = (string?)msg["password"] ?? "";
		string secret = (string?)msg["secret"] ?? "";

		if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password) || string.IsNullOrWhiteSpace(secret))
		{
			Send(new JObject { ["type"] = "modRegisterResult", ["ok"] = false, ["error"] = "All fields required" });
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var body = new JObject { ["username"] = username, ["password"] = password, ["secret"] = secret };
				var content = new StringContent(body.ToString(), Encoding.UTF8, "application/json");
				var response = await s_httpClient.PostAsync(MASTER_SERVER_URL + "/mod/register", content);
				var respBody = await response.Content.ReadAsStringAsync();
				var respJson = JObject.Parse(respBody);

				if (response.IsSuccessStatusCode)
				{
					Send(new JObject { ["type"] = "modRegisterResult", ["ok"] = true });
				}
				else
				{
					Send(new JObject { ["type"] = "modRegisterResult", ["ok"] = false, ["error"] = (string?)respJson["error"] ?? "Registration failed" });
				}
			}
			catch (Exception ex)
			{
				Send(new JObject { ["type"] = "modRegisterResult", ["ok"] = false, ["error"] = ex.Message });
			}
		});
	}

	private void OnModLogin(JObject msg)
	{
		string username = ((string?)msg["username"] ?? "").Trim().ToLower();
		string password = (string?)msg["password"] ?? "";

		if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password))
		{
			Send(new JObject { ["type"] = "modLoginResult", ["ok"] = false, ["error"] = "Username and password required" });
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var body = new JObject { ["username"] = username, ["password"] = password };
				var content = new StringContent(body.ToString(), Encoding.UTF8, "application/json");
				var response = await s_httpClient.PostAsync(MASTER_SERVER_URL + "/mod/login", content);
				var respBody = await response.Content.ReadAsStringAsync();
				var respJson = JObject.Parse(respBody);

				if (response.IsSuccessStatusCode)
				{
					m_modToken = (string?)respJson["token"];
					m_modUsername = (string?)respJson["username"];
					Send(new JObject { ["type"] = "modLoginResult", ["ok"] = true, ["username"] = m_modUsername });
					PushModTokenToClients(m_modToken);
				}
				else
				{
					Send(new JObject { ["type"] = "modLoginResult", ["ok"] = false, ["error"] = (string?)respJson["error"] ?? "Login failed" });
				}
			}
			catch (Exception ex)
			{
				Send(new JObject { ["type"] = "modLoginResult", ["ok"] = false, ["error"] = ex.Message });
			}
		});
	}

	private void OnModLogout()
	{
		if (m_modToken == null) return;

		string token = m_modToken;
		m_modToken = null;
		m_modUsername = null;

		Task.Run(async () =>
		{
			try
			{
				var request = new HttpRequestMessage(HttpMethod.Post, MASTER_SERVER_URL + "/mod/logout");
				request.Headers.Add("Authorization", "Bearer " + token);
				await s_httpClient.SendAsync(request);
			}
			catch { }
		});

		Send(new JObject { ["type"] = "modLogoutResult", ["ok"] = true });
	}

	private void OnModGlobalBan(JObject msg)
	{
		if (m_modToken == null)
		{
			Send(new JObject { ["type"] = "modGlobalBanResult", ["ok"] = false, ["error"] = "Not logged in" });
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var body = new JObject
				{
					["hwid"] = (string?)msg["hwid"] ?? "",
					["components"] = msg["components"] ?? new JArray(),
					["reason"] = (string?)msg["reason"] ?? ""
				};
				var request = new HttpRequestMessage(HttpMethod.Post, MASTER_SERVER_URL + "/mod/global-ban")
				{
					Content = new StringContent(body.ToString(), Encoding.UTF8, "application/json")
				};
				request.Headers.Add("Authorization", "Bearer " + m_modToken);
				var response = await s_httpClient.SendAsync(request);
				var respBody = await response.Content.ReadAsStringAsync();
				var respJson = JObject.Parse(respBody);

				Send(new JObject { ["type"] = "modGlobalBanResult", ["ok"] = response.IsSuccessStatusCode, ["error"] = (string?)respJson["error"] });
			}
			catch (Exception ex)
			{
				Send(new JObject { ["type"] = "modGlobalBanResult", ["ok"] = false, ["error"] = ex.Message });
			}
		});
	}

	private void OnModGlobalUnban(JObject msg)
	{
		if (m_modToken == null)
		{
			Send(new JObject { ["type"] = "modGlobalUnbanResult", ["ok"] = false, ["error"] = "Not logged in" });
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var body = new JObject { ["id"] = (int)(msg["id"] ?? 0) };
				var request = new HttpRequestMessage(HttpMethod.Post, MASTER_SERVER_URL + "/mod/global-unban")
				{
					Content = new StringContent(body.ToString(), Encoding.UTF8, "application/json")
				};
				request.Headers.Add("Authorization", "Bearer " + m_modToken);
				var response = await s_httpClient.SendAsync(request);
				Send(new JObject { ["type"] = "modGlobalUnbanResult", ["ok"] = response.IsSuccessStatusCode });
			}
			catch (Exception ex)
			{
				Send(new JObject { ["type"] = "modGlobalUnbanResult", ["ok"] = false, ["error"] = ex.Message });
			}
		});
	}

	private void OnModGetGlobalBans()
	{
		if (m_modToken == null)
		{
			Send(new JObject { ["type"] = "modGlobalBansList", ["ok"] = false, ["bans"] = new JArray() });
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var request = new HttpRequestMessage(HttpMethod.Get, MASTER_SERVER_URL + "/mod/global-bans");
				request.Headers.Add("Authorization", "Bearer " + m_modToken);
				var response = await s_httpClient.SendAsync(request);
				var respBody = await response.Content.ReadAsStringAsync();
				var respJson = JObject.Parse(respBody);
				respJson["type"] = "modGlobalBansList";
				Send(respJson);
			}
			catch
			{
				Send(new JObject { ["type"] = "modGlobalBansList", ["ok"] = false, ["bans"] = new JArray() });
			}
		});
	}

	private void OnModBanServer(JObject msg)
	{
		if (m_modToken == null)
		{
			Send(new JObject { ["type"] = "modBanServerResult", ["ok"] = false, ["error"] = "Not logged in" });
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var body = new JObject
				{
					["ip"] = (string?)msg["ip"] ?? "",
					["reason"] = (string?)msg["reason"] ?? ""
				};
				var request = new HttpRequestMessage(HttpMethod.Post, MASTER_SERVER_URL + "/mod/ban-server")
				{
					Content = new StringContent(body.ToString(), Encoding.UTF8, "application/json")
				};
				request.Headers.Add("Authorization", "Bearer " + m_modToken);
				var response = await s_httpClient.SendAsync(request);
				var respBody = await response.Content.ReadAsStringAsync();
				var respJson = JObject.Parse(respBody);
				Send(new JObject { ["type"] = "modBanServerResult", ["ok"] = response.IsSuccessStatusCode, ["error"] = (string?)respJson["error"] });
			}
			catch (Exception ex)
			{
				Send(new JObject { ["type"] = "modBanServerResult", ["ok"] = false, ["error"] = ex.Message });
			}
		});
	}

	private void OnModUnbanServer(JObject msg)
	{
		if (m_modToken == null)
		{
			Send(new JObject { ["type"] = "modUnbanServerResult", ["ok"] = false, ["error"] = "Not logged in" });
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var body = new JObject { ["ip"] = (string?)msg["ip"] ?? "" };
				var request = new HttpRequestMessage(HttpMethod.Post, MASTER_SERVER_URL + "/mod/unban-server")
				{
					Content = new StringContent(body.ToString(), Encoding.UTF8, "application/json")
				};
				request.Headers.Add("Authorization", "Bearer " + m_modToken);
				var response = await s_httpClient.SendAsync(request);
				Send(new JObject { ["type"] = "modUnbanServerResult", ["ok"] = response.IsSuccessStatusCode });
			}
			catch (Exception ex)
			{
				Send(new JObject { ["type"] = "modUnbanServerResult", ["ok"] = false, ["error"] = ex.Message });
			}
		});
	}

	private void OnModGetBannedServers()
	{
		if (m_modToken == null)
		{
			Send(new JObject { ["type"] = "modBannedServersList", ["ok"] = false, ["servers"] = new JArray() });
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var request = new HttpRequestMessage(HttpMethod.Get, MASTER_SERVER_URL + "/mod/banned-servers");
				request.Headers.Add("Authorization", "Bearer " + m_modToken);
				var response = await s_httpClient.SendAsync(request);
				var respBody = await response.Content.ReadAsStringAsync();
				var respJson = JObject.Parse(respBody);
				respJson["type"] = "modBannedServersList";
				Send(respJson);
			}
			catch
			{
				Send(new JObject { ["type"] = "modBannedServersList", ["ok"] = false, ["servers"] = new JArray() });
			}
		});
	}

	// called from the stdout handler when a player authenticates on the side channel
	// checks the global ban database and kicks them if banned
	private void CheckGlobalBan(int pid, string playerName, string hwid, JArray? components)
	{
		if (!m_useGlobalBanDB) return;

		Task.Run(async () =>
		{
			try
			{
				var body = new JObject
				{
					["hwid"] = hwid,
					["components"] = components ?? new JArray()
				};
				var content = new StringContent(body.ToString(), Encoding.UTF8, "application/json");
				var response = await s_httpClient.PostAsync(MASTER_SERVER_URL + "/bans/check", content);
				if (!response.IsSuccessStatusCode) return; // fail open

				var respBody = await response.Content.ReadAsStringAsync();
				var respJson = JObject.Parse(respBody);

				if ((bool)(respJson["banned"] ?? false))
				{
					string reason = (string?)respJson["reason"] ?? "Globally banned";

					lock (m_instanceLock)
					{
						if (m_instances.TryGetValue(pid, out var instance))
						{
							instance.SendCommand($"Server.KickPlayer {playerName}");
						}
					}
					Send(new JObject
					{
						["type"] = "instanceOutput",
						["pid"] = pid,
						["line"] = $"[GCBDB] Kicked globally banned player: {playerName} ({reason})"
					});
				}
			}
			catch
			{
				// fail open - if master is down, let them in
			}
		});
	}

	// global ban a player by name - looks up their hwid/components from JS-provided data
	// bans locally on the server AND submits to global ban database
	private void OnModGlobalBanPlayer(JObject msg)
	{
		if (m_modToken == null)
		{
			SendStatus("Not logged in as global moderator", "error");
			return;
		}

		string playerName = (string?)msg["player"] ?? "";
		string reason = (string?)msg["reason"] ?? "";
		string hwid = (string?)msg["hwid"] ?? "";
		var components = msg["components"] as JArray ?? new JArray();

		if (string.IsNullOrEmpty(playerName))
		{
			SendStatus("No player specified", "error");
			return;
		}

		if (string.IsNullOrEmpty(hwid) && components.Count == 0)
		{
			SendStatus("No HWID data available for " + playerName + " - they may not have a side-channel connection", "error");
			return;
		}

		Task.Run(async () =>
		{
			try
			{
				var body = new JObject
				{
					["hwid"] = hwid,
					["components"] = components,
					["reason"] = reason
				};
				var request = new HttpRequestMessage(HttpMethod.Post, MASTER_SERVER_URL + "/mod/global-ban")
				{
					Content = new StringContent(body.ToString(), Encoding.UTF8, "application/json")
				};
				request.Headers.Add("Authorization", "Bearer " + m_modToken);
				var response = await s_httpClient.SendAsync(request);

				if (response.IsSuccessStatusCode)
				{
					SendStatus($"Globally banned {playerName}", "info");
					Send(new JObject { ["type"] = "modGlobalBanResult", ["ok"] = true });
				}
				else
				{
					var respBody = await response.Content.ReadAsStringAsync();
					var respJson = JObject.Parse(respBody);
					SendStatus("Global ban failed: " + ((string?)respJson["error"] ?? "Unknown error"), "error");
				}
			}
			catch (Exception ex)
			{
				SendStatus("Global ban failed: " + ex.Message, "error");
			}
		});
	}

	private void PushModTokenToClients(string? token)
	{
		lock (m_instanceLock)
		{
			foreach (var kvp in m_instances)
			{
				if (!kvp.Value.IsServer)
					kvp.Value.SendCommand("Cypress.SetModToken " + (token ?? ""));
			}
		}
	}
}
