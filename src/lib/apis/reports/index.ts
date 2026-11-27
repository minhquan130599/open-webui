import { WEBUI_API_BASE_URL } from '$lib/constants';

// Scheduled reports: the AI summarizes the progress of chosen channels on a schedule and sends it by email or Telegram.

export type ReportFrequency = 'daily' | 'weekly' | 'monthly' | 'custom';
export type ReportSection = 'overview' | 'due' | 'done' | 'blockers' | 'people' | 'decisions';

export type ReportConfig = {
	scope: 'all' | 'pick';
	channel_ids: string[];
	frequency: ReportFrequency;
	weekdays: number[]; // 0 = Monday … 6 = Sunday
	workdays_only: boolean;
	month_day: number; // 1–28, -1 = last day
	times: string[]; // HH:MM (several only for custom)
	period: 'since_last' | '7d' | 'last_month';
	sections: ReportSection[];
	prompt: string;
	skip_unchanged: boolean;
	email: { enabled: boolean; self: boolean; to: string[] };
	telegram: { enabled: boolean; link_ids: string[] };
};

export type ReportSchedule = {
	id: string;
	user_id: string;
	name: string;
	enabled: boolean;
	config: ReportConfig;
	when: string;
	next_run_at: number | null; // ns
	last_run_at: number | null;
	last_status: 'sent' | 'skipped' | 'error' | null;
	last_error: string | null;
	created_at: number;
	updated_at: number;
	owner?: { id: string; name: string; email: string };
};

export type ReportAccess = {
	enabled: boolean;
	can_create: boolean;
	show: boolean; // the sidebar shows the page
	allow_prompt: boolean;
	allow_outside: boolean;
	email_enabled: boolean;
	telegram_enabled: boolean;
	telegram_paused?: boolean; // reports are not sent by Telegram for now
	telegram_bot: string;
	allow_groups: boolean;
	max_schedules: number;
	max_channels: number;
	default_prompt: string;
	owners_only: boolean; // only the channels the user runs can be chosen
	email: string;
};

export type TelegramLink = {
	id: string;
	chat_id: string;
	chat_title: string;
	chat_type: 'account' | 'private' | 'group' | 'supergroup' | 'channel';
	linked_at: number;
};

export type ReceivingReport = {
	id: string;
	name: string;
	when: string;
	enabled: boolean;
	owner: string;
	channels: number | null;
};

export type ReportPreview = {
	subject: string;
	html: string;
	telegram: string;
	totals: { open: number; overdue: number; due_soon: number; done: number; blocked: number };
	channels: number;
};

const request = async (token: string, path: string, method = 'GET', body?: unknown) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/reports${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`,
			...(body === undefined ? {} : { 'Content-Type': 'application/json' })
		},
		...(body === undefined ? {} : { body: JSON.stringify(body) })
	});
	if (!res.ok) {
		const body = await res.json().catch(() => ({}));
		throw { status: res.status, detail: body?.detail ?? res.statusText };
	}
	return res.json();
};

type ScheduleForm = { name: string; enabled: boolean; config: ReportConfig };

export const getReportAccess = (token: string): Promise<ReportAccess> => request(token, '/access');
export const getReportChannels = (token: string): Promise<{ id: string; name: string }[]> =>
	request(token, '/channels');
export const getReportSchedules = (token: string): Promise<ReportSchedule[]> => request(token, '/');
export const createReportSchedule = (token: string, form: ScheduleForm): Promise<ReportSchedule> =>
	request(token, '/', 'POST', form);
export const updateReportSchedule = (token: string, id: string, form: ScheduleForm): Promise<ReportSchedule> =>
	request(token, `/${id}`, 'PUT', form);
export const deleteReportSchedule = (token: string, id: string) => request(token, `/${id}`, 'DELETE');
export const previewReport = (token: string, form: ScheduleForm): Promise<ReportPreview> =>
	request(token, '/preview', 'POST', form);
export const sendTestReport = (token: string, form: ScheduleForm): Promise<{ sent: string[]; errors: string[] }> =>
	request(token, '/test', 'POST', form);
export const getReceivingReports = (token: string): Promise<ReceivingReport[]> => request(token, '/receiving');
export const unsubscribeReport = (token: string, id: string) =>
	request(token, `/receiving/${id}/unsubscribe`, 'POST');

export const getTelegramLinks = (token: string): Promise<TelegramLink[]> => request(token, '/telegram');
export const newTelegramLink = (
	token: string
): Promise<{ id: string; code: string; bot: string; url: string; group_command: string; allow_groups: boolean }> =>
	request(token, '/telegram/link', 'POST');
export const getTelegramLinkStatus = (
	token: string,
	id: string
): Promise<{ linked: boolean; gone?: boolean; chat_title?: string; chat_type?: string }> =>
	request(token, `/telegram/link/${id}`);
export const deleteTelegramLink = (token: string, id: string) => request(token, `/telegram/${id}`, 'DELETE');

// Admin
export type ReportAdminSettings = Record<string, any>;
export const getReportAdminSettings = (token: string): Promise<ReportAdminSettings> =>
	request(token, '/admin/settings');
export const saveReportAdminSettings = (token: string, changes: Record<string, any>): Promise<ReportAdminSettings> =>
	request(token, '/admin/settings', 'POST', { changes });
export const testReportTelegram = (
	token: string,
	botToken = ''
): Promise<{ ok: boolean; username?: string; error?: string }> =>
	request(token, '/admin/telegram/test', 'POST', { token: botToken });
export const getAllReportSchedules = (token: string): Promise<ReportSchedule[]> => request(token, '/admin/schedules');
export const setReportScheduleEnabled = (token: string, id: string, enabled: boolean) =>
	request(token, `/admin/schedules/${id}/enabled?enabled=${enabled}`, 'POST');
export const runReportNow = (token: string, id: string) => request(token, `/admin/schedules/${id}/run`, 'POST');
export const deleteAnyReportSchedule = (token: string, id: string) => request(token, `/admin/schedules/${id}`, 'DELETE');
