import { WEBUI_API_BASE_URL } from '$lib/constants';

export type TrackerWorkItem = {
	id: string;
	ten: string;
	phu_trach: string[];
	phu_trach_ids?: string[]; // owner accounts, when the name matched exactly one person
	trang_thai: 'chua_bat_dau' | 'dang_lam' | 'bi_chan' | 'tam_dung' | 'hoan_thanh';
	tien_do: number | null;
	han: string | null;
	vuong_mac: string | null;
	nguon: string[];
	tao_luc?: string | null; // YYYY-MM-DD
	cap_nhat_luc?: string | null;
	hoan_thanh_luc?: string | null;
};

export type TrackerArchivedItem = TrackerWorkItem & {
	archive_kind: 'dong' | 'huy'; // closed | cancelled
	archived_at: number; // ns
	archived_by: string;
};

export type TrackerEntry = {
	noi_dung: string;
	ngay?: string | null;
	muc_do?: 'thap' | 'trung_binh' | 'cao';
	phu_trach?: string[];
	han?: string | null;
	nguon: string[];
};

export type TrackerProjectState = {
	tong_quan: string;
	hang_muc: TrackerWorkItem[];
	quyet_dinh: TrackerEntry[];
	rui_ro: TrackerEntry[];
	viec_tiep_theo: TrackerEntry[];
};

export type TrackerState = {
	channel_id: string;
	state: TrackerProjectState | null;
	markdown: string;
	version: number;
	processed_at: number | null; // ns
	pending_count: number;
	processing: boolean;
	next_attempt_at: number | null; // ns, set after a failed run
	last_error: string | null; // admins only
	archive: TrackerArchivedItem[]; // newest first
	can_manage: boolean; // channel owner or admin: may close, cancel and reopen items
};

export type TrackerItem = {
	id: string;
	source_type: string;
	source_ids: string[];
	period_start: number;
	period_end: number;
	summary: string | null;
	data: { attachments?: string[] } | null;
};

const request = async (token: string, path: string, method = 'GET', body?: unknown) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/tracker${path}`, {
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

// Returns null when the tracker is disabled on the server (404).
export const getTrackerState = async (
	token: string,
	channelId: string
): Promise<TrackerState | null> => {
	try {
		return await request(token, `/channels/${encodeURIComponent(channelId)}`);
	} catch (error: any) {
		if (error?.status === 404) return null;
		throw error;
	}
};

export const refreshTrackerState = async (token: string, channelId: string) =>
	request(token, `/channels/${encodeURIComponent(channelId)}/refresh`, 'POST');

export const getTrackerItems = async (
	token: string,
	channelId: string,
	limit = 30
): Promise<TrackerItem[]> => {
	const start = Math.floor(Date.now() / 1000) - 30 * 86400;
	return request(
		token,
		`/channels/${encodeURIComponent(channelId)}/items?start=${start}&limit=${limit}`
	);
};

const channelPath = (channelId: string) => `/channels/${encodeURIComponent(channelId)}`;

// Close ('dong') or cancel ('huy') work items. Channel owner or admin only.
export const archiveTrackerItems = async (
	token: string,
	channelId: string,
	ids: string[],
	kind: 'dong' | 'huy'
): Promise<{ archived: number }> =>
	request(token, `${channelPath(channelId)}/items/archive`, 'POST', { ids, kind });

export const closeDoneTrackerItems = async (
	token: string,
	channelId: string
): Promise<{ archived: number }> =>
	request(token, `${channelPath(channelId)}/items/close-done`, 'POST');

export const reopenTrackerItem = async (token: string, channelId: string, itemId: string) =>
	request(token, `${channelPath(channelId)}/items/${encodeURIComponent(itemId)}/reopen`, 'POST');

export type WorkBucket = 'overdue' | 'today' | 'week' | 'later' | 'no_due' | 'done';

export type MyWorkItem = {
	channel_id: string;
	channel_name: string;
	id: string;
	ten: string;
	phu_trach: string[];
	owner_ids: string[];
	trang_thai: string;
	tien_do: number | null;
	han: string | null;
	vuong_mac: string | null;
	cap_nhat_luc: string | null;
	bucket: WorkBucket;
	days_left: number | null;
	channel_owner_id: string | null;
	can_manage: boolean; // channel owner or admin: may complete & close or cancel
};

export type MyWork = {
	user: { id: string; name: string };
	today: string;
	items: MyWorkItem[];
	can_view_team: boolean;
};

export type TeamMember = {
	id: string;
	name: string;
	email: string;
	counts: Record<WorkBucket, number>;
};

export type Team = {
	channels: { id: string; name: string }[];
	people: TeamMember[];
	unassigned: number;
};

export type ReminderSettings = {
	enable: boolean;
	time: string;
	day_before: boolean;
	last_run?: string | null;
	timezone?: string;
};

// Open work items of the user, or of a member of a channel the viewer owns (admins: anyone).
export const getMyWork = async (token: string, userId?: string): Promise<MyWork> =>
	request(token, `/my-work${userId ? `?user_id=${encodeURIComponent(userId)}` : ''}`);

export const getTeam = async (token: string): Promise<Team> => request(token, '/team');

export const getReminderSettings = async (token: string): Promise<ReminderSettings> =>
	request(token, '/reminders/settings');

export const updateReminderSettings = async (
	token: string,
	settings: ReminderSettings
): Promise<ReminderSettings> =>
	request(token, '/reminders/settings', 'POST', {
		enable: settings.enable,
		time: settings.time,
		day_before: settings.day_before
	});

export const sendRemindersNow = async (token: string): Promise<{ created: number }> =>
	request(token, '/reminders/send', 'POST');

export type BoardChannel = { id: string; name: string; can_manage: boolean };

export type Board = {
	mode: 'mine' | 'team';
	today: string;
	can_view_team: boolean;
	channels: BoardChannel[];
	people: Record<string, { name: string; email: string }>;
	items: MyWorkItem[];
};

// "My work" page: items grouped by channel, for the viewer ('mine') or the channels they manage ('team').
export const getBoard = async (token: string, mode: 'mine' | 'team'): Promise<Board> =>
	request(token, `/board?mode=${mode}`);
