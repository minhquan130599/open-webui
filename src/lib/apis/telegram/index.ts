import { WEBUI_API_BASE_URL } from '$lib/constants';

export type TelegramAccount = {
	telegram_user_id: string;
	username: string | null;
	display_name: string | null;
	linked_at: number;
	last_seen_at: number | null;
};

export type TelegramMe = {
	available: boolean;
	can_ask: boolean;
	reports: boolean;
	bot: string;
	account: TelegramAccount | null;
};

export type TelegramAdminAccount = TelegramAccount & { user_id: string; user_name: string; user_email: string };

const request = async (token: string, path: string, method = 'GET', body?: unknown) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/telegram${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`,
			...(body === undefined ? {} : { 'Content-Type': 'application/json' })
		},
		...(body === undefined ? {} : { body: JSON.stringify(body) })
	});
	if (!res.ok) {
		const data = await res.json().catch(() => ({}));
		throw { status: res.status, detail: data?.detail ?? res.statusText };
	}
	return res.json();
};

export const getTelegramMe = (token: string): Promise<TelegramMe> => request(token, '/me');
export const newTelegramAccountLink = (
	token: string
): Promise<{ code: string; url: string; bot: string; minutes: number }> => request(token, '/me/link', 'POST');
export const getTelegramAccountLinkStatus = (
	token: string,
	code: string
): Promise<{ linked: boolean; gone?: boolean; account: TelegramAccount | null }> =>
	request(token, '/me/link/status', 'POST', { code });
export const unlinkTelegramAccount = (token: string) => request(token, '/me', 'DELETE');

export const getTelegramAdminSettings = (token: string): Promise<Record<string, any>> =>
	request(token, '/admin/settings');
export const saveTelegramAdminSettings = (token: string, changes: Record<string, any>): Promise<Record<string, any>> =>
	request(token, '/admin/settings', 'POST', { changes });
export const testTelegramBot = (token: string, botToken = '') =>
	request(token, '/admin/test', 'POST', { token: botToken });
export const getTelegramStatus = (token: string): Promise<Record<string, any>> => request(token, '/admin/status');
export const getTelegramAccounts = (token: string): Promise<TelegramAdminAccount[]> =>
	request(token, '/admin/accounts');
export const adminUnlinkTelegram = (token: string, userId: string) =>
	request(token, `/admin/accounts/${userId}`, 'DELETE');
