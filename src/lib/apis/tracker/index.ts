import { WEBUI_API_BASE_URL } from '$lib/constants';

export type TrackerWorkItem = {
	ten: string;
	phu_trach: string[];
	trang_thai: 'chua_bat_dau' | 'dang_lam' | 'bi_chan' | 'tam_dung' | 'hoan_thanh';
	tien_do: number | null;
	han: string | null;
	vuong_mac: string | null;
	nguon: string[];
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

const request = async (token: string, path: string, method = 'GET') => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/tracker${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
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
