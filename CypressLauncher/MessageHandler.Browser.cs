#nullable enable
using System;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace CypressLauncher;

public partial class MessageHandler
{
	private void OnFetchBrowser()
	{
		Task.Run(async () =>
		{
			try
			{
				var resp = await s_httpClient.GetStringAsync(MASTER_SERVER_URL + "/servers");
				var parsed = JObject.Parse(resp);
				parsed["type"] = "browserList";
				Send(parsed);
			}
			catch
			{
				Send(new JObject { ["type"] = "browserList", ["servers"] = new JArray(), ["error"] = true });
			}
		});
	}

	private void OnFetchBrowserIcon(string key)
	{
		if (string.IsNullOrWhiteSpace(key)) return;
		Task.Run(async () =>
		{
			try
			{
				var response = await s_httpClient.GetAsync(MASTER_SERVER_URL + "/icon?key=" + Uri.EscapeDataString(key));
				if (!response.IsSuccessStatusCode)
				{
					Send(new JObject { ["type"] = "browserIcon", ["key"] = key });
					return;
				}
				var resp = await response.Content.ReadAsStringAsync();
				var parsed = JObject.Parse(resp);
				parsed["type"] = "browserIcon";
				parsed["key"] = key;
				Send(parsed);
			}
			catch
			{
				try { Send(new JObject { ["type"] = "browserIcon", ["key"] = key }); } catch { }
			}
		});
	}

	private void StartHeartbeat(JObject serverData, int pid)
	{
		StopHeartbeat(pid);
		var state = new HeartbeatState(serverData);
		state.Data["pid"] = pid;
		lock (m_heartbeats) m_heartbeats[pid] = state;

		_ = SendHeartbeat(pid, isFirst: true);
		state.Timer = new System.Threading.Timer(_ => _ = SendHeartbeat(pid, isFirst: false), null, 30000, 30000);
	}

	private Task StopHeartbeat(int pid)
	{
		HeartbeatState? state;
		lock (m_heartbeats)
		{
			if (!m_heartbeats.Remove(pid, out state)) return Task.CompletedTask;
		}
		state.Timer?.Dispose();

		var data = state.Data;
		var token = state.Token;
		return Task.Run(async () =>
		{
			try
			{
				var body = new JObject
				{
					["address"] = (string?)data["address"] ?? "",
					["port"] = (int)(data["port"] ?? 14638),
					["token"] = token ?? ""
				};
				var content = new StringContent(body.ToString(), Encoding.UTF8, "application/json");
				await s_httpClient.PostAsync(MASTER_SERVER_URL + "/deregister", content);
			}
			catch { }
		});
	}

	private async Task StopAllHeartbeatsAsync()
	{
		int[] pids;
		lock (m_heartbeats) pids = m_heartbeats.Keys.ToArray();
		var tasks = pids.Select(pid => StopHeartbeat(pid));
		await Task.WhenAll(tasks);
	}

	private void UpdateHeartbeatPlayerCount(int pid, int delta)
	{
		lock (m_heartbeats)
		{
			if (m_heartbeats.TryGetValue(pid, out var state))
			{
				state.PlayerCount = Math.Max(0, state.PlayerCount + delta);
				state.Data["players"] = state.PlayerCount;
			}
		}
	}

	private async Task SendHeartbeat(int pid, bool isFirst = false)
	{
		HeartbeatState? state;
		lock (m_heartbeats)
		{
			if (!m_heartbeats.TryGetValue(pid, out state)) return;
		}
		try
		{
			var payload = new JObject(state.Data);

			if (state.Token != null)
				payload["token"] = state.Token;

			// to survive master server restarts
			state.Count++;
			if (!isFirst && state.Count % 5 != 0)
				payload.Remove("icon");

			var content = new StringContent(payload.ToString(), Encoding.UTF8, "application/json");
			var response = await s_httpClient.PostAsync(MASTER_SERVER_URL + "/heartbeat", content);
			if (response.IsSuccessStatusCode)
			{
				var respBody = await response.Content.ReadAsStringAsync();
				var respJson = JObject.Parse(respBody);
				var token = (string?)respJson["token"];
				if (!string.IsNullOrEmpty(token))
					state.Token = token;
			}
		}
		catch { }
	}
}
