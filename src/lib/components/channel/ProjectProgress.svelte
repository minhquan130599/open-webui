<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { flip } from 'svelte/animate';
	import { fly, slide } from 'svelte/transition';
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';

	import { user } from '$lib/stores';
	import {
		archiveTrackerItems,
		closeDoneTrackerItems,
		getTrackerItems,
		getTrackerState,
		refreshTrackerState,
		reopenTrackerItem,
		type TrackerItem,
		type TrackerState,
		type TrackerWorkItem
	} from '$lib/apis/tracker';

	import ConfirmDialog from '../common/ConfirmDialog.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import XMark from '../icons/XMark.svelte';
	import ArrowPath from '../icons/ArrowPath.svelte';
	import EllipsisHorizontal from '../icons/EllipsisHorizontal.svelte';

	const i18n: any = getContext('i18n');

	export let channel;
	export let onClose = () => {};
	// Opens the message a source points to ("msg:<id>").
	export let onSource = (messageId: string) => {};

	const POLL_MS = 20_000;

	type Filter = 'all' | 'mine' | 'overdue' | 'blocked' | 'active' | 'done';

	let tracker: TrackerState | null = null;
	let items: TrackerItem[] = [];
	let loading = true;
	let refreshing = false;
	let timer: ReturnType<typeof setInterval> | null = null;

	// Open sections, grouping and "hide completed" are a per-browser view preference.
	const VIEW_KEY = 'projectProgressView.v2';
	const readView = () => {
		try {
			return JSON.parse(localStorage.getItem(VIEW_KEY) ?? '{}');
		} catch {
			return {};
		}
	};
	let expanded: Record<string, boolean> = readView().expanded ?? {};
	let hideCompleted: boolean = readView().hideCompleted ?? false;
	// 'task': one list; 'person': the same items grouped by owner.
	let groupBy: 'task' | 'person' = readView().groupBy ?? 'task';
	$: try {
		localStorage.setItem(VIEW_KEY, JSON.stringify({ expanded, hideCompleted, groupBy }));
	} catch {}

	let filter: Filter = 'all';
	let busy = false;
	let menuFor: string | null = null;
	let menuUp = false;
	// Progress bars grow from zero once per opened channel.
	let barsReady = false;
	// Pending confirmation: cancel one item, or close every completed item.
	let confirm: { kind: 'cancel'; item: TrackerWorkItem } | { kind: 'close-done' } | null = null;
	let showConfirm = false;

	const toggle = (key: string) => {
		expanded = { ...expanded, [key]: !expanded[key] };
		if (key === 'history' && expanded.history) load();
	};

	const STATUS: Record<string, { label: string; pill: string; color: string }> = {
		chua_bat_dau: {
			label: 'Not started',
			pill: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300',
			color: 'bg-gray-400 dark:bg-gray-500'
		},
		dang_lam: {
			label: 'In progress',
			pill: 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
			color: 'bg-blue-500'
		},
		bi_chan: {
			label: 'Blocked',
			pill: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
			color: 'bg-red-500'
		},
		tam_dung: {
			label: 'Paused',
			pill: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
			color: 'bg-amber-500'
		},
		hoan_thanh: {
			label: 'Done',
			pill: 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300',
			color: 'bg-green-500'
		}
	};
	const RISK: Record<string, { label: string; className: string }> = {
		cao: { label: 'High', className: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300' },
		trung_binh: {
			label: 'Medium',
			className: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
		},
		thap: {
			label: 'Low',
			className: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
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

	const isDone = (item: TrackerWorkItem) => item.trang_thai === 'hoan_thanh';
	const daysLeft = (item: TrackerWorkItem) =>
		item.han ? dayjs(item.han).startOf('day').diff(dayjs().startOf('day'), 'day') : null;
	const isOverdue = (item: TrackerWorkItem) =>
		!isDone(item) && (daysLeft(item) ?? 0) < 0 && item.han !== null;
	const needsAttention = (item: TrackerWorkItem) => {
		if (isDone(item)) return false;
		const days = daysLeft(item);
		return item.trang_thai === 'bi_chan' || (days !== null && days <= 1);
	};
	const isMine = (item: TrackerWorkItem) =>
		!!$user?.id && (item.phu_trach_ids ?? []).includes($user.id);

	// Tag text and color for the due date: "Overdue 3 days", "Today", "Tomorrow", "5 days left".
	const dueTag = (item: TrackerWorkItem) => {
		const days = daysLeft(item);
		if (days === null || isDone(item)) return null;
		if (days < 0)
			return {
				text: $i18n.t('Overdue {{count}} days', { count: -days }),
				className: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300'
			};
		if (days <= 1)
			return {
				text: days === 0 ? $i18n.t('Today') : $i18n.t('Tomorrow'),
				className: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
			};
		return {
			text: $i18n.t('{{count}} days left', { count: days }),
			className: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
		};
	};
	const barColor = (item: TrackerWorkItem) => {
		if (isDone(item)) return 'bg-green-500';
		if (item.trang_thai === 'bi_chan' || isOverdue(item)) return 'bg-red-500';
		const days = daysLeft(item);
		if (days !== null && days <= 1) return 'bg-amber-500';
		return 'bg-blue-500';
	};

	$: all = tracker?.state?.hang_muc ?? [];
	$: open = all.filter((item) => !isDone(item));
	$: completedCount = all.length - open.length;
	$: withProgress = open.filter((item) => item.tien_do !== null && item.tien_do !== undefined);
	$: average = withProgress.length
		? Math.round(
				withProgress.reduce((sum, item) => sum + (item.tien_do ?? 0), 0) / withProgress.length
			)
		: 0;
	$: stats = [
		{
			key: 'active' as Filter,
			label: 'In progress',
			count: open.filter((i) => i.trang_thai === 'dang_lam').length,
			dot: 'bg-blue-500'
		},
		{
			key: 'overdue' as Filter,
			label: 'Overdue',
			count: all.filter(isOverdue).length,
			dot: 'bg-red-500'
		},
		{
			key: 'blocked' as Filter,
			label: 'Blocked',
			count: open.filter((i) => i.trang_thai === 'bi_chan').length,
			dot: 'bg-amber-500'
		},
		{ key: 'done' as Filter, label: 'Done', count: completedCount, dot: 'bg-green-500' }
	];
	$: attention = all
		.filter(needsAttention)
		.sort((a, b) => (daysLeft(a) ?? 99) - (daysLeft(b) ?? 99));

	const matches = (item: TrackerWorkItem, current: Filter, hide: boolean) => {
		if (current === 'mine') return isMine(item) && (!hide || !isDone(item));
		if (current === 'overdue') return isOverdue(item);
		if (current === 'blocked') return item.trang_thai === 'bi_chan';
		if (current === 'active') return item.trang_thai === 'dang_lam';
		if (current === 'done') return isDone(item);
		return !hide || !isDone(item);
	};
	$: visibleItems = all.filter((item) => matches(item, filter, hideCompleted));
	$: canManage = tracker?.can_manage ?? false;
	$: archive = tracker?.archive ?? [];

	// An item with several owners appears under each of them; items without an owner come last.
	const groupByPerson = (list: TrackerWorkItem[], unassigned: string) => {
		const groups = new Map<string, TrackerWorkItem[]>();
		for (const item of list) {
			for (const owner of item.phu_trach?.length ? item.phu_trach : [unassigned]) {
				groups.set(owner, [...(groups.get(owner) ?? []), item]);
			}
		}
		return [...groups.entries()]
			.map(([name, items]) => ({ name, items, overdue: items.filter(isOverdue).length }))
			.sort((a, b) =>
				a.name === unassigned
					? 1
					: b.name === unassigned
						? -1
						: b.overdue - a.overdue || a.name.localeCompare(b.name, 'vi')
			);
	};
	$: people = groupBy === 'person' ? groupByPerson(visibleItems, $i18n.t('Unassigned')) : [];

	const setFilter = (value: Filter) =>
		(filter = filter === value && value !== 'all' ? 'all' : value);

	const showItem = async (item: TrackerWorkItem) => {
		filter = 'all';
		hideCompleted = false;
		groupBy = 'task';
		await tick();
		document
			.getElementById(`progress-item-${item.id}`)
			?.scrollIntoView({ behavior: 'smooth', block: 'center' });
	};

	const act = async (action: () => Promise<unknown>, success: string) => {
		busy = true;
		menuFor = null;
		try {
			await action();
			toast.success(success);
			await load();
		} catch (error: any) {
			toast.error(`${error?.detail ?? error}`);
		} finally {
			busy = false;
		}
	};
	const closeItem = (item: TrackerWorkItem) =>
		act(
			() => archiveTrackerItems(localStorage.token, channel.id, [item.id], 'dong'),
			$i18n.t('Work item closed')
		);
	const cancelItem = (item: TrackerWorkItem) =>
		act(
			() => archiveTrackerItems(localStorage.token, channel.id, [item.id], 'huy'),
			$i18n.t('Work item cancelled')
		);
	const closeDone = () =>
		act(
			() => closeDoneTrackerItems(localStorage.token, channel.id),
			$i18n.t('Completed items closed')
		);
	const reopen = (itemId: string) =>
		act(
			() => reopenTrackerItem(localStorage.token, channel.id, itemId),
			$i18n.t('Work item reopened')
		);

	const fromNs = (ns: number | null) =>
		ns ? dayjs(Math.floor(ns / 1e6)).format('DD/MM HH:mm') : '';
	const sourceIds = (sources: string[] = []) =>
		sources.filter((s) => s.startsWith('msg:')).map((s) => s.slice(4));

	const load = async () => {
		if (!channel?.id) return;
		try {
			tracker = await getTrackerState(localStorage.token, channel.id);
			if (expanded.history) {
				items = (await getTrackerItems(localStorage.token, channel.id)).reverse();
			}
		} catch (error: any) {
			console.error(error);
		} finally {
			loading = false;
			if (!barsReady && tracker?.state) {
				await tick();
				requestAnimationFrame(() => (barsReady = true));
			}
		}
	};

	const refresh = async () => {
		refreshing = true;
		try {
			await refreshTrackerState(localStorage.token, channel.id);
			toast.success($i18n.t('Progress update requested'));
		} catch (error: any) {
			toast.error(`${error?.detail ?? error}`);
		} finally {
			refreshing = false;
		}
	};

	$: (channel?.id, (loading = true), (barsReady = false), (filter = 'all'), load());

	const RING = 2 * Math.PI * 34;

	const onVisible = () => document.visibilityState === 'visible' && load();

	onMount(() => {
		timer = setInterval(load, POLL_MS);
		document.addEventListener('visibilitychange', onVisible);
		window.addEventListener('focus', onVisible);
	});
	onDestroy(() => {
		if (timer) clearInterval(timer);
		document.removeEventListener('visibilitychange', onVisible);
		window.removeEventListener('focus', onVisible);
	});
</script>

<svelte:window
	on:click={() => (menuFor = null)}
	on:keydown={(e) => e.key === 'Escape' && (menuFor = null)}
/>

<div class="flex flex-col h-full w-full bg-white dark:bg-gray-900">
	<div class="flex items-start justify-between px-4 pt-3 pb-2 shrink-0">
		<div class="min-w-0">
			<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
				{$i18n.t('Work progress')}
			</div>
			<div
				class="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-gray-500 dark:text-gray-400"
			>
				<span
					class="rounded-full bg-blue-50 px-1.5 py-px text-[0.625rem] font-semibold uppercase tracking-wide text-blue-700 dark:bg-blue-950 dark:text-blue-300"
					>{$i18n.t('AI summary')}</span
				>
				{#if tracker?.processed_at}
					{$i18n.t('Updated {{time}}', { time: fromNs(tracker.processed_at) })}
				{:else}
					{$i18n.t('Not summarized yet')}
				{/if}
				{#if tracker?.processing}
					· {$i18n.t('Summarizing...')}
				{:else if tracker?.pending_count}
					· {$i18n.t('{{count}} new messages pending', { count: tracker.pending_count })}
				{/if}
			</div>
			{#if !tracker?.processing && tracker?.next_attempt_at}
				<div class="mt-0.5 text-xs text-red-600 dark:text-red-400 break-words">
					{$i18n.t('Last summary failed, retrying at {{time}}', {
						time: fromNs(tracker.next_attempt_at)
					})}
					{#if tracker.last_error}
						<Tooltip content={tracker.last_error}>
							<span class="underline decoration-dotted cursor-help">{$i18n.t('Details')}</span>
						</Tooltip>
					{/if}
				</div>
			{/if}
		</div>
		<div class="flex items-center gap-0.5 text-gray-500 dark:text-gray-400">
			<Tooltip content={$i18n.t('Update now')}>
				<button
					class="flex size-7 items-center justify-center rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
					aria-label={$i18n.t('Update now')}
					type="button"
					disabled={refreshing}
					on:click={refresh}
				>
					<ArrowPath className="size-3.5 {refreshing ? 'animate-spin' : ''}" strokeWidth="1.5" />
				</button>
			</Tooltip>
			<button
				class="flex size-7 items-center justify-center rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
				aria-label={$i18n.t('Close')}
				type="button"
				on:click={onClose}
			>
				<XMark className="size-4" />
			</button>
		</div>
	</div>

	<div class="flex-1 overflow-y-auto px-3 pb-4 text-sm flex flex-col gap-3 [&>*]:shrink-0">
		{#if loading && !tracker}
			<!-- Skeleton while the first state loads -->
			<div class="flex flex-col gap-3 animate-pulse" aria-busy="true">
				<div class="h-24 rounded-2xl bg-gray-100 dark:bg-gray-850"></div>
				<div class="h-16 rounded-xl bg-gray-100 dark:bg-gray-850"></div>
				<div class="h-20 rounded-xl bg-gray-100 dark:bg-gray-850"></div>
				<div class="h-20 rounded-xl bg-gray-100 dark:bg-gray-850"></div>
			</div>
		{:else if !tracker?.state}
			<div class="text-gray-500 dark:text-gray-400 py-6 text-center text-xs">
				{$i18n.t(
					'No summary yet. Progress is summarized automatically a short while after new messages are posted.'
				)}
			</div>
		{:else}
			{@const state = tracker.state}

			<!-- Summary: average progress of open items and counts that filter the list -->
			<div
				class="grid grid-cols-[4.5rem_1fr] items-center gap-3.5 rounded-2xl bg-gray-50 dark:bg-gray-850 p-3"
			>
				<div class="relative size-[4.5rem]" aria-label={$i18n.t('average')}>
					<svg viewBox="0 0 80 80" class="size-full -rotate-90">
						<circle
							cx="40"
							cy="40"
							r="34"
							fill="none"
							stroke-width="7"
							class="stroke-gray-200 dark:stroke-gray-700"
						/>
						<circle
							cx="40"
							cy="40"
							r="34"
							fill="none"
							stroke-width="7"
							stroke-linecap="round"
							class="stroke-blue-500 transition-[stroke-dashoffset] duration-1000 ease-out"
							stroke-dasharray={RING}
							stroke-dashoffset={RING * (1 - (barsReady ? average : 0) / 100)}
						/>
					</svg>
					<div class="absolute inset-0 grid place-items-center text-center leading-none">
						<div>
							<div class="text-lg font-semibold tabular-nums text-gray-900 dark:text-gray-100">
								{average}%
							</div>
							<div class="mt-0.5 text-[0.625rem] text-gray-500">{$i18n.t('average')}</div>
						</div>
					</div>
				</div>
				<div class="min-w-0">
					<div class="mb-1 text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('{{count}} open work items', { count: open.length })}
					</div>
					<div class="grid grid-cols-2 gap-x-1 gap-y-0.5">
						{#each stats as stat (stat.key)}
							<button
								type="button"
								class="-ml-1.5 flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-left text-xs transition {filter ===
								stat.key
									? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white'
									: 'text-gray-500 hover:bg-white hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-white'}"
								aria-pressed={filter === stat.key}
								on:click={() => setFilter(stat.key)}
							>
								<span class="size-2 shrink-0 rounded-full {stat.dot}"></span>
								<span class="font-semibold tabular-nums text-gray-900 dark:text-gray-100"
									>{stat.count}</span
								>
								<span class="truncate">{$i18n.t(stat.label)}</span>
							</button>
						{/each}
					</div>
				</div>
			</div>

			<!-- Needs attention: overdue, due today or tomorrow, blocked -->
			{#if attention.length}
				<div class="overflow-hidden rounded-xl border border-red-100 dark:border-red-900/40">
					<div
						class="flex items-center gap-1.5 bg-red-50 px-3 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-wide text-red-700 dark:bg-red-950/50 dark:text-red-300"
					>
						<svg
							class="size-3.5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
							><path d="M12 9v4M12 17h.01" /><path
								d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"
							/></svg
						>
						{$i18n.t('Needs attention')} ({attention.length})
					</div>
					<ul class="flex flex-col p-1">
						{#each attention as item (item.id ?? item.ten)}
							{@const tag = dueTag(item)}
							<li>
								<button
									type="button"
									class="grid w-full grid-cols-[3px_1fr_auto_auto] items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-gray-50 dark:hover:bg-gray-850"
									on:click={() => showItem(item)}
								>
									<span
										class="h-6 w-[3px] rounded-full {isOverdue(item) ||
										item.trang_thai === 'bi_chan'
											? 'bg-red-500'
											: 'bg-amber-500'}"
									></span>
									<span class="truncate text-xs text-gray-800 dark:text-gray-200" title={item.ten}
										>{item.ten}</span
									>
									{#if item.trang_thai === 'bi_chan' && !isOverdue(item)}
										<span
											class="whitespace-nowrap rounded-full px-1.5 py-0.5 text-[0.625rem] font-medium {STATUS
												.bi_chan.pill}">{$i18n.t('Blocked')}</span
										>
									{:else if tag}
										<span
											class="whitespace-nowrap rounded-full px-1.5 py-0.5 text-[0.625rem] font-medium {tag.className}"
											>{tag.text}</span
										>
									{/if}
									<span class="flex">
										{#each item.phu_trach.slice(0, 3) as name, i}
											<span
												class="grid size-5 place-items-center rounded-full border-2 border-white text-[0.5625rem] font-semibold text-white dark:border-gray-900 {avatarColor(
													name
												)} {i ? '-ml-1.5' : ''}"
												title={name}>{initial(name)}</span
											>
										{/each}
									</span>
								</button>
							</li>
						{/each}
					</ul>
				</div>
			{/if}

			{#if all.length}
				<!-- Filters and grouping -->
				<div class="flex flex-wrap items-center justify-between gap-1.5">
					<div class="flex flex-wrap gap-1" role="group" aria-label={$i18n.t('Filter')}>
						{#each [['all', 'All'], ['mine', 'Mine'], ['overdue', 'Overdue'], ['blocked', 'Blocked']] as [value, label]}
							<button
								type="button"
								class="rounded-full border px-2.5 py-0.5 text-xs transition {filter === value
									? 'border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900'
									: 'border-gray-200 text-gray-500 hover:text-gray-900 dark:border-gray-800 dark:text-gray-400 dark:hover:text-white'}"
								aria-pressed={filter === value}
								on:click={() => (filter = value as Filter)}>{$i18n.t(label)}</button
							>
						{/each}
					</div>
					<div
						class="flex shrink-0 rounded-lg bg-gray-50 dark:bg-gray-850 p-0.5 text-[0.6875rem]"
						role="group"
						aria-label={$i18n.t('Group work items')}
					>
						{#each [['task', $i18n.t('By task')], ['person', $i18n.t('By person')]] as [value, label]}
							<button
								type="button"
								class="px-1.5 py-0.5 rounded-md transition {groupBy === value
									? 'bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-sm'
									: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
								aria-pressed={groupBy === value}
								on:click={() => (groupBy = value)}>{label}</button
							>
						{/each}
					</div>
				</div>

				<div
					class="-mb-1 flex items-center justify-between gap-2 px-0.5 text-[0.6875rem] font-semibold uppercase tracking-wide text-gray-500"
				>
					<span>{$i18n.t('Work items')} ({visibleItems.length})</span>
					{#if completedCount}
						<span class="flex items-center gap-2 normal-case tracking-normal font-medium">
							{#if filter === 'all' || filter === 'mine'}
								<button
									type="button"
									class="hover:text-gray-900 dark:hover:text-white"
									on:click={() => (hideCompleted = !hideCompleted)}
								>
									{hideCompleted
										? $i18n.t('Show completed ({{count}})', { count: completedCount })
										: $i18n.t('Hide completed ({{count}})', { count: completedCount })}
								</button>
							{/if}
							{#if canManage}
								<button
									type="button"
									class="text-gray-800 hover:underline disabled:opacity-50 dark:text-gray-200"
									disabled={busy}
									on:click={() => {
										confirm = { kind: 'close-done' };
										showConfirm = true;
									}}
								>
									{$i18n.t('Close completed ({{count}})', { count: completedCount })}
								</button>
							{/if}
						</span>
					{/if}
				</div>

				{#if visibleItems.length === 0}
					<div class="py-4 text-center text-xs text-gray-500">
						{$i18n.t('No work items match the filter.')}
					</div>
				{:else if groupBy === 'task'}
					<ul class="flex flex-col gap-2">
						{#each visibleItems as item (item.id ?? item.ten)}
							{@const tag = dueTag(item)}
							{@const status = STATUS[item.trang_thai] ?? STATUS.dang_lam}
							<li
								id="progress-item-{item.id}"
								class="group/item relative rounded-xl border border-gray-100 py-2.5 pl-4 pr-2.5 transition-colors hover:border-gray-200 dark:border-gray-800 dark:hover:border-gray-700"
								animate:flip={{ duration: 250 }}
								out:fly={{ x: 16, duration: 220 }}
							>
								<span
									class="absolute left-0 top-2.5 bottom-2.5 w-[3px] rounded-r-full {status.color}"
									aria-hidden="true"
								></span>
								<div class="flex items-start justify-between gap-2">
									<div
										class="min-w-0 break-words text-[0.8125rem] font-medium leading-snug text-gray-900 dark:text-gray-100"
									>
										{item.ten}
									</div>
									<div class="flex shrink-0 items-center gap-0.5">
										<span class="rounded-full px-2 py-0.5 text-[0.6875rem] {status.pill}">
											{$i18n.t(status.label)}
										</span>
										{#if canManage && item.id}
											<div class="relative">
												<button
													type="button"
													class="flex size-6 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-gray-800 dark:hover:text-white {menuFor ===
													item.id
														? 'opacity-100'
														: 'opacity-100 md:opacity-0 md:group-hover/item:opacity-100 md:group-focus-within/item:opacity-100'}"
													aria-label={$i18n.t('Options')}
													aria-haspopup="menu"
													aria-expanded={menuFor === item.id}
													disabled={busy}
													on:click|stopPropagation={(e) => {
														menuUp =
															e.currentTarget.getBoundingClientRect().bottom >
															window.innerHeight - 120;
														menuFor = menuFor === item.id ? null : item.id;
													}}
												>
													<EllipsisHorizontal className="size-4" />
												</button>
												{#if menuFor === item.id}
													<div
														class="absolute right-0 {menuUp
															? 'bottom-7'
															: 'top-7'} z-30 flex min-w-36 flex-col rounded-xl border border-gray-100 bg-white p-1 shadow-lg dark:border-gray-800 dark:bg-gray-900"
														role="menu"
														transition:slide={{ duration: 120 }}
													>
														<button
															type="button"
															role="menuitem"
															class="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left text-xs text-green-700 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-950/40"
															on:click|stopPropagation={() => closeItem(item)}
															><span aria-hidden="true">✓</span>{$i18n.t(
																'Complete & close'
															)}</button
														>
														<button
															type="button"
															role="menuitem"
															class="rounded-lg px-2.5 py-1.5 text-left text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
															on:click|stopPropagation={() => {
																menuFor = null;
																confirm = { kind: 'cancel', item };
																showConfirm = true;
															}}
															><span aria-hidden="true" class="mr-1.5">✕</span>{$i18n.t(
																'Cancel item'
															)}</button
														>
													</div>
												{/if}
											</div>
										{/if}
									</div>
								</div>
								{#if item.tien_do !== null && item.tien_do !== undefined}
									<div class="mt-2 flex items-center gap-2">
										<div
											class="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800"
										>
											<div
												class="h-full rounded-full transition-[width] duration-1000 ease-out {barColor(
													item
												)}"
												style="width: {barsReady ? item.tien_do : 0}%"
											></div>
										</div>
										<span class="w-8 text-right text-xs tabular-nums text-gray-500"
											>{item.tien_do}%</span
										>
									</div>
								{/if}
								<div
									class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500 dark:text-gray-400"
								>
									{#if item.phu_trach?.length}
										<span class="flex min-w-0 items-center gap-1.5">
											<span class="flex shrink-0">
												{#each item.phu_trach.slice(0, 3) as name, i}
													<span
														class="grid size-5 place-items-center rounded-full border-2 border-white text-[0.5625rem] font-semibold text-white dark:border-gray-900 {avatarColor(
															name
														)} {i ? '-ml-1.5' : ''}"
														title={name}>{initial(name)}</span
													>
												{/each}
											</span>
											<span class="truncate">{item.phu_trach.join(', ')}</span>
										</span>
									{/if}
									{#if tag}
										<span
											class="whitespace-nowrap rounded-full px-1.5 py-0.5 text-[0.6875rem] {tag.className}"
											title={item.han ? dayjs(item.han).format('DD/MM/YYYY') : ''}>{tag.text}</span
										>
									{:else if !item.han && !isDone(item)}
										<span class="text-[0.6875rem] text-gray-400">{$i18n.t('No due date')}</span>
									{/if}
									{#each sourceIds(item.nguon) as id, i}
										<button
											type="button"
											class="underline-offset-2 hover:underline"
											on:click={() => onSource(id)}
										>
											{$i18n.t('Source')}
											{i + 1}
										</button>
									{/each}
								</div>
								{#if item.vuong_mac}
									<div
										class="mt-2 rounded-lg bg-red-50 px-2 py-1 text-xs leading-snug text-red-700 dark:bg-red-950/50 dark:text-red-300"
									>
										{$i18n.t('Blocker')}: {item.vuong_mac}
									</div>
								{/if}
							</li>
						{/each}
					</ul>
				{:else}
					<div class="flex flex-col gap-2">
						{#each people as person (person.name)}
							<section
								class="flex flex-col gap-2 rounded-xl border border-gray-100 px-3 py-2.5 dark:border-gray-800"
							>
								<div class="flex items-center gap-2">
									<span
										class="grid size-6 shrink-0 place-items-center rounded-full text-[0.625rem] font-semibold text-white {avatarColor(
											person.name
										)}">{initial(person.name)}</span
									>
									<span
										class="min-w-0 flex-1 truncate text-[0.8125rem] font-medium text-gray-900 dark:text-gray-100"
										>{person.name}</span
									>
									<span class="shrink-0 text-xs text-gray-500">
										{$i18n.t('{{count}} items', { count: person.items.length })}
										{#if person.overdue}
											· <span class="text-red-600 dark:text-red-400"
												>{$i18n.t('{{count}} overdue', { count: person.overdue })}</span
											>
										{/if}
									</span>
								</div>
								<ul class="flex flex-col gap-1.5">
									{#each person.items as item (item.id ?? item.ten)}
										{@const tag = dueTag(item)}
										<li class="grid grid-cols-[1fr_4rem_auto] items-center gap-2 text-xs">
											<button
												type="button"
												class="truncate text-left text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
												title={item.ten}
												on:click={() => showItem(item)}>{item.ten}</button
											>
											<div class="h-1 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
												<div
													class="h-full rounded-full transition-[width] duration-1000 ease-out {barColor(
														item
													)}"
													style="width: {barsReady ? (item.tien_do ?? 0) : 0}%"
												></div>
											</div>
											{#if isDone(item)}
												<span
													class="whitespace-nowrap rounded-full px-1.5 py-0.5 text-[0.625rem] {STATUS
														.hoan_thanh.pill}">{$i18n.t('Done')}</span
												>
											{:else if tag}
												<span
													class="whitespace-nowrap rounded-full px-1.5 py-0.5 text-[0.625rem] {tag.className}"
													>{tag.text}</span
												>
											{:else}
												<span class="text-[0.625rem] text-gray-400">—</span>
											{/if}
										</li>
									{/each}
								</ul>
							</section>
						{/each}
					</div>
				{/if}
			{/if}

			<!-- Secondary sections, collapsed by default -->
			{@const sections = [
				{ key: 'tong_quan', title: 'Overview', count: null, show: !!state.tong_quan },
				{
					key: 'rui_ro',
					title: 'Risks',
					count: state.rui_ro?.length ?? 0,
					show: !!state.rui_ro?.length
				},
				{
					key: 'quyet_dinh',
					title: 'Decisions',
					count: state.quyet_dinh?.length ?? 0,
					show: !!state.quyet_dinh?.length
				},
				{
					key: 'viec_tiep_theo',
					title: 'Next steps',
					count: state.viec_tiep_theo?.length ?? 0,
					show: !!state.viec_tiep_theo?.length
				}
			].filter((s) => s.show)}
			<div
				class="flex flex-col divide-y divide-gray-100 border-y border-gray-100 dark:divide-gray-800 dark:border-gray-800"
			>
				{#each sections as section (section.key)}
					<div>
						<button
							type="button"
							class="flex w-full items-center gap-2 px-1 py-2.5 text-left text-[0.8125rem] font-medium text-gray-800 hover:text-gray-950 dark:text-gray-200 dark:hover:text-white"
							aria-expanded={!!expanded[section.key]}
							on:click={() => toggle(section.key)}
						>
							<svg
								class="size-3 text-gray-400 transition-transform {expanded[section.key]
									? 'rotate-90'
									: ''}"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="2.4"
								aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg
							>
							{$i18n.t(section.title)}
							{#if section.count !== null}
								<span class="ml-auto text-xs font-normal text-gray-500">{section.count}</span>
							{/if}
						</button>
						{#if expanded[section.key]}
							<div
								class="px-1 pb-3 text-[0.8125rem] leading-relaxed"
								transition:slide={{ duration: 150 }}
							>
								{#if section.key === 'tong_quan'}
									<p class="text-gray-700 dark:text-gray-300">{state.tong_quan}</p>
								{:else if section.key === 'rui_ro'}
									<ul class="flex flex-col gap-1.5">
										{#each state.rui_ro as entry}
											{@const risk = RISK[entry.muc_do ?? 'trung_binh'] ?? RISK.trung_binh}
											<li class="grid grid-cols-[auto_1fr] items-baseline gap-2">
												<span
													class="whitespace-nowrap rounded-full px-1.5 py-0.5 text-[0.625rem] font-medium {risk.className}"
													>{$i18n.t(risk.label)}</span
												>
												<span class="text-gray-700 dark:text-gray-300">{entry.noi_dung}</span>
											</li>
										{/each}
									</ul>
								{:else if section.key === 'quyet_dinh'}
									<ul
										class="ml-1.5 flex flex-col gap-2.5 border-l-2 border-gray-100 pl-3.5 dark:border-gray-800"
									>
										{#each state.quyet_dinh as entry}
											<li class="relative text-gray-700 dark:text-gray-300">
												<span
													class="absolute -left-[1.3rem] top-1.5 size-2.5 rounded-full border-2 border-gray-300 bg-white dark:border-gray-600 dark:bg-gray-900"
												></span>
												{#if entry.ngay}
													<div class="text-[0.6875rem] tabular-nums text-gray-500">
														{dayjs(entry.ngay).format('DD/MM/YYYY')}
													</div>
												{/if}
												{entry.noi_dung}
											</li>
										{/each}
									</ul>
								{:else if section.key === 'viec_tiep_theo'}
									<ul class="flex flex-col gap-1.5">
										{#each state.viec_tiep_theo as entry}
											<li
												class="grid grid-cols-[0.875rem_1fr] gap-2 text-gray-700 dark:text-gray-300"
											>
												<span
													class="mt-1 size-3.5 rounded border-[1.5px] border-gray-300 dark:border-gray-600"
												></span>
												<span>
													{entry.noi_dung}
													{#if entry.phu_trach?.length || entry.han}
														<span class="text-xs text-gray-500">
															({[
																entry.phu_trach?.join(', '),
																entry.han ? dayjs(entry.han).format('DD/MM/YYYY') : ''
															]
																.filter(Boolean)
																.join(' · ')})
														</span>
													{/if}
												</span>
											</li>
										{/each}
									</ul>
								{/if}
							</div>
						{/if}
					</div>
				{/each}

				{#if archive.length}
					<div>
						<button
							type="button"
							class="flex w-full items-center gap-2 px-1 py-2.5 text-left text-[0.8125rem] font-medium text-gray-800 hover:text-gray-950 dark:text-gray-200 dark:hover:text-white"
							aria-expanded={!!expanded.archive}
							on:click={() => toggle('archive')}
						>
							<svg
								class="size-3 text-gray-400 transition-transform {expanded.archive
									? 'rotate-90'
									: ''}"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="2.4"
								aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg
							>
							{$i18n.t('Closed / cancelled')}
							<span class="ml-auto text-xs font-normal text-gray-500">{archive.length}</span>
						</button>
						{#if expanded.archive}
							<ul class="flex flex-col gap-2 px-1 pb-3" transition:slide={{ duration: 150 }}>
								{#each archive as entry (entry.id)}
									<li class="flex items-start justify-between gap-2 text-xs">
										<div class="min-w-0">
											<div
												class="break-words {entry.archive_kind === 'huy'
													? 'text-gray-500 line-through decoration-gray-400 dark:text-gray-400'
													: 'text-gray-700 dark:text-gray-300'}"
											>
												{entry.ten}
											</div>
											<div class="mt-0.5 flex flex-wrap items-center gap-1.5 text-gray-400">
												<span
													class="rounded-full px-1.5 py-px text-[0.625rem] {entry.archive_kind ===
													'huy'
														? STATUS.bi_chan.pill
														: STATUS.hoan_thanh.pill}"
												>
													{entry.archive_kind === 'huy'
														? `✕ ${$i18n.t('Cancelled')}`
														: `✓ ${$i18n.t('Completed (closed)')}`}
												</span>
												{fromNs(entry.archived_at)}
												{#if entry.phu_trach?.length}· {entry.phu_trach.join(', ')}{/if}
											</div>
										</div>
										{#if canManage}
											<button
												type="button"
												class="shrink-0 rounded-md px-1.5 py-0.5 text-gray-600 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-50 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white"
												disabled={busy}
												on:click={() => reopen(entry.id)}
											>
												{$i18n.t('Reopen')}
											</button>
										{/if}
									</li>
								{/each}
							</ul>
						{/if}
					</div>
				{/if}

				<div>
					<button
						type="button"
						class="flex w-full items-center gap-2 px-1 py-2.5 text-left text-[0.8125rem] font-medium text-gray-800 hover:text-gray-950 dark:text-gray-200 dark:hover:text-white"
						aria-expanded={!!expanded.history}
						on:click={() => toggle('history')}
					>
						<svg
							class="size-3 text-gray-400 transition-transform {expanded.history
								? 'rotate-90'
								: ''}"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2.4"
							aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg
						>
						{$i18n.t('Update history')}
						<span class="ml-auto text-xs font-normal text-gray-500">{$i18n.t('30 days')}</span>
					</button>
					{#if expanded.history}
						<ul
							class="mb-3 ml-2.5 flex flex-col gap-2.5 border-l-2 border-gray-100 pl-3.5 dark:border-gray-800"
							transition:slide={{ duration: 150 }}
						>
							{#each items as entry}
								<li class="relative text-xs">
									<span
										class="absolute -left-[1.3rem] top-1 size-2.5 rounded-full border-2 border-gray-300 bg-white dark:border-gray-600 dark:bg-gray-900"
									></span>
									<div class="tabular-nums text-gray-500">{fromNs(entry.period_end)}</div>
									<div class="text-gray-700 dark:text-gray-300">{entry.summary}</div>
								</li>
							{:else}
								<li class="text-xs text-gray-500">{$i18n.t('No updates in the last 30 days')}</li>
							{/each}
						</ul>
					{/if}
				</div>
			</div>
		{/if}
	</div>
</div>

<ConfirmDialog
	bind:show={showConfirm}
	title={confirm?.kind === 'cancel'
		? $i18n.t('Cancel work item')
		: $i18n.t('Close completed items')}
	message={confirm?.kind === 'cancel'
		? $i18n.t(
				'Cancel "{{name}}"? It leaves the progress panel and reports; you can reopen it later.',
				{
					name: confirm.item.ten
				}
			)
		: $i18n.t(
				'Close {{count}} completed items? They leave the progress panel and reports; you can reopen them later.',
				{ count: completedCount }
			)}
	onConfirm={() => {
		// The dialog closes before calling this, so read the pending action here, then clear it.
		const pending = confirm;
		confirm = null;
		if (pending?.kind === 'cancel') cancelItem(pending.item);
		else if (pending?.kind === 'close-done') closeDone();
	}}
	on:cancel={() => (confirm = null)}
/>
