<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { slide } from 'svelte/transition';
	import { toast } from 'svelte-sonner';

	import { WEBUI_NAME, config, mobile, showSidebar, user } from '$lib/stores';
	import {
		archiveTrackerItems,
		getBoard,
		type Board,
		type BoardChannel,
		type MyWorkItem,
		type WorkBucket
	} from '$lib/apis/tracker';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import SearchIcon from '$lib/components/layout/Sidebar/icons/Search.svelte';

	const i18n: any = getContext('i18n');

	type Mode = 'mine' | 'team';

	// Preserve dynamically translated labels for i18n extraction.
	// t('Overdue') t('Due today') t('Next 7 days') t('Later') t('No due date') t('Done, waiting to be closed')
	const BUCKETS: { key: WorkBucket; label: string; dot: string; bar: string }[] = [
		{ key: 'overdue', label: 'Overdue', dot: 'bg-red-500', bar: 'bg-red-500' },
		{ key: 'today', label: 'Due today', dot: 'bg-amber-500', bar: 'bg-amber-500' },
		{ key: 'week', label: 'Next 7 days', dot: 'bg-blue-500', bar: 'bg-blue-500' },
		{ key: 'later', label: 'Later', dot: 'bg-gray-400', bar: 'bg-gray-400 dark:bg-gray-500' },
		{
			key: 'no_due',
			label: 'No due date',
			dot: 'bg-gray-300',
			bar: 'bg-gray-300 dark:bg-gray-600'
		},
		{ key: 'done', label: 'Done, waiting to be closed', dot: 'bg-green-500', bar: 'bg-green-500' }
	];
	const TILES: { key: WorkBucket; label: string; text: string; ring: string; line: string }[] = [
		{
			key: 'overdue',
			label: 'Overdue',
			text: 'text-red-600 dark:text-red-400',
			ring: 'ring-red-500',
			line: 'bg-red-500'
		},
		{
			key: 'today',
			label: 'Due today',
			text: 'text-amber-600 dark:text-amber-400',
			ring: 'ring-amber-500',
			line: 'bg-amber-500'
		},
		{
			key: 'week',
			label: 'Next 7 days',
			text: 'text-blue-600 dark:text-blue-400',
			ring: 'ring-blue-500',
			line: 'bg-blue-500'
		},
		{
			key: 'no_due',
			label: 'No due date',
			text: 'text-gray-700 dark:text-gray-300',
			ring: 'ring-gray-400',
			line: 'bg-gray-400'
		}
	];
	const STATUS: Record<string, { label: string; pill: string; stripe: string }> = {
		chua_bat_dau: {
			label: 'Not started',
			pill: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300',
			stripe: 'bg-gray-400 dark:bg-gray-500'
		},
		dang_lam: {
			label: 'In progress',
			pill: 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
			stripe: 'bg-blue-500'
		},
		bi_chan: {
			label: 'Blocked',
			pill: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
			stripe: 'bg-red-500'
		},
		tam_dung: {
			label: 'Paused',
			pill: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
			stripe: 'bg-amber-500'
		},
		hoan_thanh: {
			label: 'Done',
			pill: 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300',
			stripe: 'bg-green-500'
		}
	};
	const AVATAR_COLORS = [
		'bg-violet-500',
		'bg-cyan-600',
		'bg-pink-500',
		'bg-orange-500',
		'bg-emerald-600',
		'bg-indigo-500',
		'bg-rose-500',
		'bg-teal-600'
	];
	const avatarColor = (name: string) =>
		AVATAR_COLORS[[...name].reduce((sum, c) => sum + c.charCodeAt(0), 0) % AVATAR_COLORS.length];
	const initial = (name: string) => (name.trim().split(/\s+/).pop() ?? '?').charAt(0).toUpperCase();
	const RING = 2 * Math.PI * 17;

	// Open channels/members and the chosen view are remembered per browser.
	const VIEW_KEY = 'myWorkView.v2';
	const readView = () => {
		try {
			return JSON.parse(localStorage.getItem(VIEW_KEY) ?? '{}');
		} catch {
			return {};
		}
	};
	const saved = readView();
	let mode: Mode = saved.mode === 'team' ? 'team' : 'mine';
	let open: Record<string, boolean> = saved.open ?? {};
	let openMember: Record<string, boolean> = saved.openMember ?? {};
	$: try {
		localStorage.setItem(VIEW_KEY, JSON.stringify({ mode, open, openMember }));
	} catch {}

	let board: Board | null = null;
	let loading = true;
	let error = '';
	let query = '';
	let filter: WorkBucket | null = null;
	let showDone: Record<string, boolean> = {};
	let menuFor: string | null = null;
	let menuUp = false;
	let busy = false;
	let barsReady = false;
	let pendingCancel: MyWorkItem | null = null;
	let showConfirm = false;
	let timer: ReturnType<typeof setInterval> | null = null;

	const load = async (silent = false): Promise<void> => {
		if (!silent) loading = true;
		error = '';
		try {
			const data = await getBoard(localStorage.token, mode);
			if (mode === 'team' && !data.can_view_team) {
				mode = 'mine';
				return load(silent);
			}
			board = data;
			if (!barsReady) {
				await tick();
				requestAnimationFrame(() => (barsReady = true));
			}
		} catch (e: any) {
			error = e?.detail ?? String(e);
		} finally {
			loading = false;
		}
	};

	const setMode = (value: Mode) => {
		if (value === mode) return;
		mode = value;
		filter = null;
		board = null;
		barsReady = false;
		load();
	};

	const normalize = (text = '') =>
		text.toLocaleLowerCase('vi').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd');

	const isMine = (item: MyWorkItem) => !!$user?.id && item.owner_ids.includes($user.id);
	const isOrphan = (item: MyWorkItem) => item.phu_trach.length === 0;
	const count = (items: MyWorkItem[], bucket: WorkBucket) =>
		items.filter((i) => i.bucket === bucket).length;
	const average = (items: MyWorkItem[]) => {
		const withProgress = items.filter(
			(i) => i.bucket !== 'done' && i.tien_do !== null && i.tien_do !== undefined
		);
		return withProgress.length
			? Math.round(withProgress.reduce((sum, i) => sum + (i.tien_do ?? 0), 0) / withProgress.length)
			: 0;
	};
	const groups = (items: MyWorkItem[]) =>
		BUCKETS.map((b) => ({ ...b, items: items.filter((i) => i.bucket === b.key) })).filter(
			(g) => g.items.length > 0
		);

	$: searched = (board?.items ?? []).filter(
		(item) =>
			!query.trim() ||
			normalize(
				`${item.ten} ${item.channel_name} ${item.phu_trach.join(' ')} ${item.vuong_mac ?? ''}`
			).includes(normalize(query.trim()))
	);
	// The summary tiles count the viewer's own work in "mine", everything in "team".
	$: counted = mode === 'mine' ? searched.filter(isMine) : searched;
	$: visible = filter ? searched.filter((i) => i.bucket === filter) : searched;
	$: channels = (board?.channels ?? [])
		.map((channel) => ({ channel, items: visible.filter((i) => i.channel_id === channel.id) }))
		.filter(({ items }) => items.length > 0 || (mode === 'team' && !filter && !query.trim()))
		.sort(
			(a, b) =>
				count(b.items, 'overdue') - count(a.items, 'overdue') ||
				count(b.items, 'today') - count(a.items, 'today') ||
				a.channel.name.localeCompare(b.channel.name, 'vi')
		);
	$: openCount = counted.filter((i) => i.bucket !== 'done').length;
	$: channelCount = new Set(counted.map((i) => i.channel_id)).size;

	// Team view: one group per owner account; names the tracker could not link to an account keep their
	// own group. Unassigned items are listed separately.
	const members = (items: MyWorkItem[]) => {
		const byOwner = new Map<
			string,
			{ key: string; name: string; email: string; items: MyWorkItem[] }
		>();
		for (const item of items) {
			if (isOrphan(item)) continue;
			const owners = item.owner_ids.length
				? item.owner_ids.map((id) => ({
						key: id,
						name: board?.people[id]?.name ?? id,
						email: board?.people[id]?.email ?? ''
					}))
				: item.phu_trach.map((name) => ({
						key: `name:${name}`,
						name,
						email: $i18n.t('Not linked to an account')
					}));
			for (const owner of owners) {
				const entry = byOwner.get(owner.key) ?? { ...owner, items: [] as MyWorkItem[] };
				entry.items.push(item);
				byOwner.set(owner.key, entry);
			}
		}
		return [...byOwner.values()].sort(
			(a, b) =>
				count(b.items, 'overdue') - count(a.items, 'overdue') || a.name.localeCompare(b.name, 'vi')
		);
	};

	const GRAY_TAG = 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300';
	const dueTag = (item: MyWorkItem) => {
		const days = item.days_left;
		if (item.bucket === 'done') return null;
		if (days === null || days === undefined) return { text: $i18n.t('No due date'), cls: GRAY_TAG };
		if (days < 0)
			return {
				text: $i18n.t('Overdue {{count}} days', { count: -days }),
				cls: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300'
			};
		if (days <= 1)
			return {
				text: days === 0 ? $i18n.t('Today') : $i18n.t('Tomorrow'),
				cls: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
			};
		return { text: $i18n.t('{{count}} days left', { count: days }), cls: GRAY_TAG };
	};
	const barColor = (item: MyWorkItem) =>
		item.bucket === 'done'
			? 'bg-green-500'
			: item.trang_thai === 'bi_chan' || item.bucket === 'overdue'
				? 'bg-red-500'
				: item.bucket === 'today'
					? 'bg-amber-500'
					: 'bg-blue-500';

	const archive = async (item: MyWorkItem, kind: 'dong' | 'huy') => {
		busy = true;
		menuFor = null;
		try {
			await archiveTrackerItems(localStorage.token, item.channel_id, [item.id], kind);
			toast.success(kind === 'dong' ? $i18n.t('Work item closed') : $i18n.t('Work item cancelled'));
			await load(true);
		} catch (e: any) {
			toast.error(`${e?.detail ?? e}`);
		} finally {
			busy = false;
		}
	};

	const openAll = () => {
		open = { ...open, ...Object.fromEntries((board?.channels ?? []).map((c) => [c.id, true])) };
	};
	const pickFilter = (key: WorkBucket) => {
		filter = filter === key ? null : key;
		if (filter) openAll();
	};
	let lastQuery = '';
	$: if (query.trim() && query !== lastQuery) {
		lastQuery = query;
		openAll();
	}

	const onVisible = () => document.visibilityState === 'visible' && load(true);

	onMount(() => {
		load();
		timer = setInterval(() => load(true), 30_000);
		document.addEventListener('visibilitychange', onVisible);
		window.addEventListener('focus', onVisible);
	});
	onDestroy(() => {
		if (timer) clearInterval(timer);
		document.removeEventListener('visibilitychange', onVisible);
		window.removeEventListener('focus', onVisible);
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('My work')} / {$WEBUI_NAME}
	</title>
</svelte:head>

<svelte:window
	on:click={() => (menuFor = null)}
	on:keydown={(e) => e.key === 'Escape' && (menuFor = null)}
/>

{#snippet itemRow(item: MyWorkItem)}
	{@const status = STATUS[item.trang_thai] ?? STATUS.dang_lam}
	{@const tag = dueTag(item)}
	{@const key = item.channel_id + item.id}
	<li
		class="relative grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 rounded-xl border border-gray-100 bg-white py-2.5 pl-4 pr-2 transition-colors hover:border-gray-200 dark:border-gray-800 dark:bg-gray-900 dark:hover:border-gray-700"
	>
		<span class="absolute left-0 top-2.5 bottom-2.5 w-[3px] rounded-r-full {status.stripe}"></span>
		<div class="min-w-0">
			<div
				class="break-words text-[0.8125rem] font-medium leading-snug text-gray-900 dark:text-gray-100"
			>
				{item.ten}
			</div>
			<div
				class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500 dark:text-gray-400"
			>
				{#if item.tien_do !== null && item.tien_do !== undefined}
					<span class="h-1.5 w-28 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
						<span
							class="block h-full rounded-full transition-[width] duration-1000 ease-out {barColor(
								item
							)}"
							style="width: {barsReady ? item.tien_do : 0}%"
						></span>
					</span>
					<span class="tabular-nums">{item.tien_do}%</span>
				{/if}
				{#if tag}
					<span class="whitespace-nowrap rounded-full px-1.5 py-0.5 text-[0.6875rem] {tag.cls}"
						>{tag.text}</span
					>
				{/if}
				{#if mode === 'mine' && item.phu_trach.length > 1}
					<span>{$i18n.t('with {{names}}', { names: item.phu_trach.join(', ') })}</span>
				{/if}
			</div>
			{#if item.vuong_mac}
				<div class="mt-1.5 text-xs text-red-600 dark:text-red-400">
					{$i18n.t('Blocker')}: {item.vuong_mac}
				</div>
			{/if}
		</div>
		<div class="flex shrink-0 items-center gap-0.5">
			<span class="rounded-full px-2 py-0.5 text-[0.6875rem] {status.pill}"
				>{$i18n.t(status.label)}</span
			>
			{#if item.can_manage}
				<div class="relative">
					<button
						type="button"
						class="flex size-7 items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-gray-800 dark:hover:text-white"
						aria-label={$i18n.t('Options')}
						aria-haspopup="menu"
						aria-expanded={menuFor === key}
						disabled={busy}
						on:click|stopPropagation={(e) => {
							menuUp = e.currentTarget.getBoundingClientRect().bottom > window.innerHeight - 120;
							menuFor = menuFor === key ? null : key;
						}}
					>
						<EllipsisHorizontal className="size-4" />
					</button>
					{#if menuFor === key}
						<div
							class="absolute right-0 {menuUp
								? 'bottom-8'
								: 'top-8'} z-30 flex min-w-44 flex-col rounded-xl border border-gray-100 bg-white p-1 shadow-lg dark:border-gray-800 dark:bg-gray-900"
							role="menu"
							transition:slide={{ duration: 120 }}
						>
							<button
								type="button"
								role="menuitem"
								class="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left text-xs text-green-700 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-950/40"
								on:click|stopPropagation={() => archive(item, 'dong')}
								><span aria-hidden="true">✓</span>{$i18n.t('Complete & close')}</button
							>
							<button
								type="button"
								role="menuitem"
								class="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
								on:click|stopPropagation={() => {
									menuFor = null;
									pendingCancel = item;
									showConfirm = true;
								}}><span aria-hidden="true">✕</span>{$i18n.t('Cancel item')}</button
							>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</li>
{/snippet}

{#snippet grouped(items: MyWorkItem[], key: string)}
	{#each groups(items) as group (group.key)}
		{#if group.key === 'done' && !showDone[key]}
			<button
				type="button"
				class="block px-1.5 pt-2 text-xs text-gray-500 hover:text-gray-900 dark:hover:text-white"
				on:click={() => (showDone = { ...showDone, [key]: true })}
			>
				+ {$i18n.t('{{count}} completed items waiting to be closed', {
					count: group.items.length
				})}
			</button>
		{:else}
			<div
				class="flex items-center gap-2 px-1.5 pb-1.5 pt-3 text-[0.6875rem] font-semibold uppercase tracking-wide text-gray-500"
			>
				<span class="size-1.5 rounded-full {group.dot}"></span>
				{$i18n.t(group.label)} · {group.items.length}
			</div>
			<ul class="flex flex-col gap-1.5">
				{#each group.items as item (item.channel_id + item.id)}
					{@render itemRow(item)}
				{/each}
			</ul>
		{/if}
	{:else}
		<div class="py-4 text-center text-xs text-gray-500">{$i18n.t('No work items.')}</div>
	{/each}
{/snippet}

{#snippet orphanBlock(channel: BoardChannel, items: MyWorkItem[])}
	{@const orphans = items.filter(isOrphan)}
	{@const key = `${channel.id}:__none__`}
	{#if channel.can_manage && orphans.length}
		<div class="mt-2 rounded-xl border border-dashed border-amber-400 dark:border-amber-700">
			<button
				type="button"
				class="grid w-full grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-amber-50/60 dark:hover:bg-amber-950/20"
				aria-expanded={!!openMember[key]}
				on:click={() => (openMember = { ...openMember, [key]: !openMember[key] })}
			>
				<span
					class="grid size-8 place-items-center rounded-full border-[1.5px] border-dashed border-amber-500 text-sm font-semibold text-amber-600 dark:text-amber-400"
					>?</span
				>
				<span class="min-w-0">
					<span class="block text-[0.8125rem] font-medium text-gray-900 dark:text-gray-100"
						>{$i18n.t('No owner yet')}</span
					>
					<span class="block truncate text-xs text-gray-500"
						>{$i18n.t('Nobody is assigned, so nobody updates their progress')}</span
					>
				</span>
				<span class="flex items-center gap-2">
					<span
						class="rounded-full bg-amber-50 px-2 py-0.5 text-[0.6875rem] font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300"
						>{$i18n.t('{{count}} items', { count: orphans.length })}</span
					>
					<svg
						class="size-3 text-gray-400 transition-transform {openMember[key] ? 'rotate-90' : ''}"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2.4"><path d="M9 6l6 6-6 6" /></svg
					>
				</span>
			</button>
			{#if openMember[key]}
				<div
					class="border-t border-dashed border-amber-300 px-2.5 pb-2.5 dark:border-amber-800"
					transition:slide={{ duration: 150 }}
				>
					<div
						class="mt-2.5 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-gray-700 dark:bg-amber-950/40 dark:text-gray-300"
					>
						<b>{$i18n.t('How to assign:')}</b>
						{$i18n.t('post in #{{channel}} with the person and the task, for example:', {
							channel: channel.name
						})}
						<code
							class="rounded-md border border-gray-200 bg-white px-1.5 py-px text-[0.6875rem] dark:border-gray-700 dark:bg-gray-900"
							>{$i18n.t('assign @Name to "{{task}}", due 20/11', { task: orphans[0].ten })}</code
						>.
						{$i18n.t(
							'At the next summary (or Update now in the progress panel) the task moves to that person, who then sees it in My work and gets due reminders. Use ⋯ → Cancel for work no longer needed, ⋯ → Complete & close for finished work.'
						)}
					</div>
					{@render grouped(orphans, key)}
				</div>
			{/if}
		</div>
	{/if}
{/snippet}

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<nav class="px-1.5 pt-1.5 pb-2 md:px-3 md:pt-2 drag-region select-none shrink-0">
		<div class="flex items-center gap-1">
			{#if $mobile}
				<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center self-start">
					<Tooltip content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}>
						<button
							class="cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
							on:click={() => showSidebar.set(!$showSidebar)}
						>
							<div class="self-center p-1.5">
								<SidebarIcon className="size-4" />
							</div>
						</button>
					</Tooltip>
				</div>
			{/if}
			<div class="flex w-full items-center justify-between gap-2 py-1 px-1">
				<div class="text-sm text-gray-500 dark:text-gray-400 truncate">
					{$WEBUI_NAME} /
					<span class="text-gray-900 dark:text-gray-100">{$i18n.t('My work')}</span>
				</div>
				{#if board?.can_view_team || mode === 'team'}
					<div
						class="flex shrink-0 gap-0.5 rounded-xl p-0.5 bg-gray-50 dark:bg-gray-850 border border-gray-100 dark:border-gray-800 text-xs"
						role="group"
						aria-label={$i18n.t('View')}
					>
						{#each [['mine', $i18n.t('Mine')], ['team', $i18n.t('Members')]] as [value, label]}
							<button
								type="button"
								class="px-2.5 py-1 rounded-lg transition {mode === value
									? 'bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-sm font-medium'
									: 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'}"
								aria-pressed={mode === value}
								on:click={() => setMode(value as Mode)}
							>
								{label}
							</button>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</nav>

	<div class="flex-1 overflow-y-auto">
		<div class="mx-auto flex max-w-4xl flex-col gap-4 px-4 pb-16 md:px-6">
			{#if !($config?.features?.enable_project_tracker ?? false)}
				<div class="mt-10 text-center text-sm text-gray-500 dark:text-gray-400">
					{$i18n.t('Work tracking is not enabled on this server.')}
				</div>
			{:else}
				<div class="mt-2 flex flex-wrap items-end justify-between gap-3">
					<div class="min-w-0">
						<h1 class="text-2xl font-semibold tracking-tight text-gray-900 dark:text-white">
							{mode === 'mine' ? $i18n.t('My work') : $i18n.t('Members')}
						</h1>
						<p class="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
							{#if mode === 'mine'}
								{$i18n.t('{{count}} open work items in {{channels}} channels', {
									count: openCount,
									channels: channelCount
								})}
							{:else if $user?.role === 'admin'}
								{$i18n.t('Work of everyone in the channels you are in.')}
							{:else}
								{$i18n.t('Work of the members in the channels you own.')}
							{/if}
						</p>
					</div>
					<label
						class="flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-2.5 dark:border-gray-800 dark:bg-gray-900"
					>
						<SearchIcon className="size-3.5 text-gray-400" strokeWidth="1.5" />
						<input
							bind:value={query}
							class="w-48 bg-transparent py-1.5 text-xs outline-none placeholder:text-gray-400"
							placeholder={$i18n.t('Search work, channels, people…')}
							aria-label={$i18n.t('Search')}
						/>
					</label>
				</div>

				<!-- Summary tiles; each filters the list -->
				<div class="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
					{#each TILES as tile (tile.key)}
						<button
							type="button"
							class="relative overflow-hidden rounded-2xl border bg-white px-3.5 py-3 text-left transition dark:bg-gray-900 {filter ===
							tile.key
								? `ring-1 ${tile.ring} border-transparent`
								: 'border-gray-100 hover:border-gray-200 dark:border-gray-800 dark:hover:border-gray-700'}"
							aria-pressed={filter === tile.key}
							on:click={() => pickFilter(tile.key)}
						>
							<div class="text-2xl font-semibold tabular-nums {tile.text}">
								{count(counted, tile.key)}
							</div>
							<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t(tile.label)}</div>
							<span class="absolute inset-x-0 bottom-0 h-[3px] opacity-70 {tile.line}"></span>
						</button>
					{/each}
				</div>

				{#if loading && !board}
					<div class="flex justify-center py-16"><Spinner className="size-5" /></div>
				{:else if error}
					<div class="text-sm text-red-600 dark:text-red-400">{error}</div>
				{:else if channels.length === 0}
					<div class="py-16 text-center text-sm text-gray-500 dark:text-gray-400">
						{(board?.items ?? []).length === 0
							? $i18n.t('No work items are assigned yet.')
							: $i18n.t('No work items match the filter.')}
					</div>
				{:else}
					<div class="flex flex-col gap-2.5">
						{#each channels as { channel, items } (channel.id)}
							{@const openItems = items.filter((i) => i.bucket !== 'done')}
							{@const avg = average(items)}
							<section
								class="rounded-2xl border border-gray-100 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900"
							>
								<button
									type="button"
									class="grid w-full grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-3 rounded-2xl px-4 py-3.5 text-left transition hover:bg-gray-50 dark:hover:bg-gray-850/60"
									aria-expanded={!!open[channel.id]}
									on:click={() => (open = { ...open, [channel.id]: !open[channel.id] })}
								>
									<span
										class="grid size-10 place-items-center rounded-xl bg-blue-50 text-lg font-bold text-blue-600 dark:bg-blue-950 dark:text-blue-400"
										>#</span
									>
									<span class="min-w-0">
										<span class="flex flex-wrap items-center gap-2">
											<span
												class="truncate text-[0.9375rem] font-semibold text-gray-900 dark:text-gray-100"
												>{channel.name}</span
											>
											{#if channel.can_manage}
												<span
													class="rounded-full border border-gray-200 px-1.5 py-px text-[0.625rem] font-semibold uppercase tracking-wide text-gray-500 dark:border-gray-700"
													>{$user?.role === 'admin'
														? $i18n.t('Admin')
														: $i18n.t('Channel owner')}</span
												>
											{/if}
										</span>
										<span class="mt-1.5 flex flex-wrap items-center gap-2.5">
											<span
												class="flex h-1.5 w-full max-w-64 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800"
												aria-hidden="true"
											>
												{#each BUCKETS as b (b.key)}
													<span
														class="h-full transition-[width] duration-700 ease-out {b.bar}"
														style="width: {barsReady && items.length
															? (count(items, b.key) / items.length) * 100
															: 0}%"
													></span>
												{/each}
											</span>
											<span class="flex flex-wrap gap-1.5 text-[0.6875rem] font-medium">
												<span class="rounded-full px-2 py-0.5 {GRAY_TAG}"
													>{$i18n.t('{{count}} open', { count: openItems.length })}</span
												>
												{#if count(items, 'overdue')}
													<span
														class="rounded-full bg-red-50 px-2 py-0.5 text-red-700 dark:bg-red-950 dark:text-red-300"
														>{$i18n.t('{{count}} overdue', {
															count: count(items, 'overdue')
														})}</span
													>
												{/if}
												{#if count(items, 'today')}
													<span
														class="rounded-full bg-amber-50 px-2 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
														>{$i18n.t('{{count}} due today', {
															count: count(items, 'today')
														})}</span
													>
												{/if}
												{#if count(items, 'done')}
													<span
														class="rounded-full bg-green-50 px-2 py-0.5 text-green-700 dark:bg-green-950 dark:text-green-300"
														>{$i18n.t('{{count}} done', { count: count(items, 'done') })}</span
													>
												{/if}
											</span>
										</span>
									</span>
									<span class="flex items-center gap-2.5">
										<span class="relative size-10" title={$i18n.t('average')}>
											<svg viewBox="0 0 42 42" class="size-full -rotate-90">
												<circle
													cx="21"
													cy="21"
													r="17"
													fill="none"
													stroke-width="5"
													class="stroke-gray-100 dark:stroke-gray-800"
												/>
												<circle
													cx="21"
													cy="21"
													r="17"
													fill="none"
													stroke-width="5"
													stroke-linecap="round"
													class="stroke-blue-500 transition-[stroke-dashoffset] duration-1000 ease-out"
													stroke-dasharray={RING}
													stroke-dashoffset={RING * (1 - (barsReady ? avg : 0) / 100)}
												/>
											</svg>
											<span
												class="absolute inset-0 grid place-items-center text-[0.625rem] font-semibold tabular-nums text-gray-700 dark:text-gray-300"
												>{avg}%</span
											>
										</span>
										<svg
											class="size-3.5 text-gray-400 transition-transform {open[channel.id]
												? 'rotate-90'
												: ''}"
											viewBox="0 0 24 24"
											fill="none"
											stroke="currentColor"
											stroke-width="2.4"><path d="M9 6l6 6-6 6" /></svg
										>
									</span>
								</button>

								{#if open[channel.id]}
									<div
										class="border-t border-gray-100 px-3 pb-3 dark:border-gray-800"
										transition:slide={{ duration: 180 }}
									>
										<div class="flex justify-end pt-2">
											<a
												href="/channels/{channel.id}"
												class="text-xs text-gray-500 hover:text-gray-900 hover:underline dark:hover:text-white"
												>{$i18n.t('Open channel')} →</a
											>
										</div>
										{#if mode === 'mine'}
											{@const assigned = items.filter((i) => !isOrphan(i))}
											{#if assigned.length}
												{@render grouped(assigned, channel.id)}
											{/if}
											{@render orphanBlock(channel, items)}
										{:else}
											{@render orphanBlock(channel, items)}
											{#each members(items) as member (member.key)}
												{@const key = `${channel.id}:${member.key}`}
												{@const memberOpen = member.items.filter((i) => i.bucket !== 'done')}
												{@const memberAvg = average(member.items)}
												<div class="mt-2 rounded-xl border border-gray-100 dark:border-gray-800">
													<button
														type="button"
														class="grid w-full grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-gray-50 dark:hover:bg-gray-850/60"
														aria-expanded={!!openMember[key]}
														on:click={() =>
															(openMember = { ...openMember, [key]: !openMember[key] })}
													>
														<span
															class="grid size-8 place-items-center rounded-full text-xs font-semibold text-white {avatarColor(
																member.name
															)}">{initial(member.name)}</span
														>
														<span class="min-w-0">
															<span
																class="block truncate text-[0.8125rem] font-medium text-gray-900 dark:text-gray-100"
																>{member.name}</span
															>
															<span class="block truncate text-xs text-gray-500"
																>{member.email}</span
															>
														</span>
														<span class="flex items-center gap-1.5 text-[0.6875rem] font-medium">
															{#if count(member.items, 'overdue')}
																<span
																	class="rounded-full bg-red-50 px-2 py-0.5 text-red-700 dark:bg-red-950 dark:text-red-300"
																	>{$i18n.t('{{count}} overdue', {
																		count: count(member.items, 'overdue')
																	})}</span
																>
															{/if}
															{#if count(member.items, 'today')}
																<span
																	class="rounded-full bg-amber-50 px-2 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
																	>{$i18n.t('{{count}} due today', {
																		count: count(member.items, 'today')
																	})}</span
																>
															{/if}
															<span class="rounded-full px-2 py-0.5 {GRAY_TAG}"
																>{$i18n.t('{{count}} open', { count: memberOpen.length })}</span
															>
															<span
																class="hidden h-1.5 w-16 overflow-hidden rounded-full bg-gray-100 sm:block dark:bg-gray-800"
																title="{memberAvg}%"
															>
																<span
																	class="block h-full rounded-full bg-blue-500 transition-[width] duration-1000 ease-out"
																	style="width: {barsReady ? memberAvg : 0}%"
																></span>
															</span>
															<svg
																class="size-3 text-gray-400 transition-transform {openMember[key]
																	? 'rotate-90'
																	: ''}"
																viewBox="0 0 24 24"
																fill="none"
																stroke="currentColor"
																stroke-width="2.4"><path d="M9 6l6 6-6 6" /></svg
															>
														</span>
													</button>
													{#if openMember[key]}
														<div
															class="border-t border-dashed border-gray-100 px-2.5 pb-2.5 dark:border-gray-800"
															transition:slide={{ duration: 150 }}
														>
															{@render grouped(member.items, key)}
														</div>
													{/if}
												</div>
											{:else}
												{#if !items.some(isOrphan)}
													<div class="py-4 text-center text-xs text-gray-500">
														{$i18n.t('No work items are assigned yet.')}
													</div>
												{/if}
											{/each}
										{/if}
									</div>
								{/if}
							</section>
						{/each}
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>

<ConfirmDialog
	bind:show={showConfirm}
	title={$i18n.t('Cancel work item')}
	message={$i18n.t(
		'Cancel "{{name}}"? It leaves the progress panel and reports; you can reopen it later.',
		{ name: pendingCancel?.ten ?? '' }
	)}
	onConfirm={() => {
		// The dialog closes before calling this, so read the pending item here, then clear it.
		const item = pendingCancel;
		pendingCancel = null;
		if (item) archive(item, 'huy');
	}}
	on:cancel={() => (pendingCancel = null)}
/>
