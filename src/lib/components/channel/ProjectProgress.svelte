<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';

	import {
		getTrackerItems,
		getTrackerState,
		refreshTrackerState,
		type TrackerItem,
		type TrackerState
	} from '$lib/apis/tracker';

	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import XMark from '../icons/XMark.svelte';
	import ArrowPath from '../icons/ArrowPath.svelte';

	const i18n: any = getContext('i18n');

	export let channel;
	export let onClose = () => {};
	// Opens the message a source points to ("msg:<id>").
	export let onSource = (messageId: string) => {};

	const POLL_MS = 20_000;

	let tracker: TrackerState | null = null;
	let items: TrackerItem[] = [];
	let loading = true;
	let refreshing = false;
	let showHistory = false;
	let timer: ReturnType<typeof setInterval> | null = null;

	const STATUS: Record<string, { label: string; className: string }> = {
		chua_bat_dau: {
			label: 'Not started',
			className: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
		},
		dang_lam: {
			label: 'In progress',
			className: 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
		},
		bi_chan: {
			label: 'Blocked',
			className: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300'
		},
		tam_dung: {
			label: 'Paused',
			className: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
		},
		hoan_thanh: {
			label: 'Done',
			className: 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
		}
	};
	const RISK: Record<string, string> = { thap: 'Low', trung_binh: 'Medium', cao: 'High' };

	const today = () => dayjs().format('YYYY-MM-DD');
	const isOverdue = (item) => item.han && item.han < today() && item.trang_thai !== 'hoan_thanh';
	const fromNs = (ns: number | null) =>
		ns ? dayjs(Math.floor(ns / 1e6)).format('DD/MM HH:mm') : '';

	const load = async () => {
		if (!channel?.id) return;
		try {
			tracker = await getTrackerState(localStorage.token, channel.id);
			if (showHistory) {
				items = (await getTrackerItems(localStorage.token, channel.id)).reverse();
			}
		} catch (error: any) {
			console.error(error);
		} finally {
			loading = false;
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

	const sourceIds = (sources: string[] = []) =>
		sources.filter((s) => s.startsWith('msg:')).map((s) => s.slice(4));

	$: if (showHistory) load();
	$: (channel?.id, (loading = true), load());

	onMount(() => {
		timer = setInterval(load, POLL_MS);
	});
	onDestroy(() => {
		if (timer) clearInterval(timer);
	});
</script>

<div class="flex flex-col h-full w-full bg-white dark:bg-gray-900">
	<div class="flex items-center justify-between px-4 pt-3 pb-2 shrink-0">
		<div class="min-w-0">
			<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
				{$i18n.t('Project progress')}
			</div>
			<div class="text-xs text-gray-500 dark:text-gray-400 truncate">
				{#if tracker?.processed_at}
					{$i18n.t('Updated {{time}}', { time: fromNs(tracker.processed_at) })}
				{:else}
					{$i18n.t('Not summarized yet')}
				{/if}
				{#if tracker?.pending_count}
					· {$i18n.t('{{count}} new messages pending', { count: tracker.pending_count })}
				{/if}
			</div>
		</div>
		<div class="flex items-center gap-1 text-gray-500 dark:text-gray-400">
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

	<div class="flex-1 overflow-y-auto px-4 pb-4 text-sm">
		{#if loading && !tracker}
			<div class="flex justify-center py-8"><Spinner className="size-5" /></div>
		{:else if !tracker?.state}
			<div class="text-gray-500 dark:text-gray-400 py-6 text-center text-xs">
				{$i18n.t(
					'No summary yet. Progress is summarized automatically a short while after new messages are posted.'
				)}
			</div>
		{:else}
			{@const state = tracker.state}
			{#if state.tong_quan}
				<p class="text-gray-700 dark:text-gray-300 mb-3 leading-relaxed">{state.tong_quan}</p>
			{/if}

			{#if state.hang_muc?.length}
				<div class="text-xs font-medium uppercase tracking-wide text-gray-500 mb-1.5">
					{$i18n.t('Work items')}
				</div>
				<ul class="flex flex-col gap-2 mb-4">
					{#each state.hang_muc as item}
						<li class="rounded-xl border border-gray-100 dark:border-gray-800 px-3 py-2">
							<div class="flex items-start justify-between gap-2">
								<div class="font-medium text-gray-900 dark:text-gray-100 min-w-0 break-words">
									{item.ten}
								</div>
								<span
									class="shrink-0 rounded-full px-2 py-0.5 text-[0.6875rem] {STATUS[item.trang_thai]
										?.className ?? STATUS.dang_lam.className}"
								>
									{$i18n.t(STATUS[item.trang_thai]?.label ?? 'In progress')}
								</span>
							</div>
							{#if item.tien_do !== null && item.tien_do !== undefined}
								<div class="flex items-center gap-2 mt-1.5">
									<div
										class="h-1.5 flex-1 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden"
									>
										<div
											class="h-full rounded-full {item.trang_thai === 'bi_chan'
												? 'bg-red-500'
												: 'bg-blue-500'}"
											style="width: {item.tien_do}%"
										/>
									</div>
									<span class="text-xs tabular-nums text-gray-500">{item.tien_do}%</span>
								</div>
							{/if}
							<div
								class="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-xs text-gray-500 dark:text-gray-400"
							>
								{#if item.phu_trach?.length}
									<span>{$i18n.t('Owner')}: {item.phu_trach.join(', ')}</span>
								{/if}
								{#if item.han}
									<span class={isOverdue(item) ? 'text-red-600 dark:text-red-400 font-medium' : ''}>
										{$i18n.t('Due')}: {dayjs(item.han).format('DD/MM/YYYY')}
										{#if isOverdue(item)}({$i18n.t('overdue')}){/if}
									</span>
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
								<div class="mt-1 text-xs text-red-600 dark:text-red-400">
									{$i18n.t('Blocker')}: {item.vuong_mac}
								</div>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}

			{#each [{ key: 'viec_tiep_theo', title: 'Next steps' }, { key: 'rui_ro', title: 'Risks' }, { key: 'quyet_dinh', title: 'Decisions' }] as section}
				{#if state[section.key]?.length}
					<div class="text-xs font-medium uppercase tracking-wide text-gray-500 mb-1.5">
						{$i18n.t(section.title)}
					</div>
					<ul class="mb-4 flex flex-col gap-1">
						{#each state[section.key] as entry}
							<li class="text-gray-700 dark:text-gray-300 leading-snug">
								• {#if entry.muc_do}
									<span
										class="text-xs font-medium {entry.muc_do === 'cao'
											? 'text-red-600 dark:text-red-400'
											: 'text-amber-600 dark:text-amber-400'}"
									>
										[{$i18n.t(RISK[entry.muc_do] ?? 'Medium')}]
									</span>
								{/if}
								{entry.noi_dung}
								{#if entry.phu_trach?.length}
									<span class="text-xs text-gray-500">— {entry.phu_trach.join(', ')}</span>
								{/if}
								{#if entry.han || entry.ngay}
									<span class="text-xs text-gray-500">
										({dayjs(entry.han || entry.ngay).format('DD/MM/YYYY')})
									</span>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			{/each}
		{/if}

		{#if tracker}
			<button
				type="button"
				class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 mt-1"
				on:click={() => (showHistory = !showHistory)}
			>
				{showHistory ? '▾' : '▸'}
				{$i18n.t('Update history')}
			</button>
			{#if showHistory}
				<ul class="mt-2 flex flex-col gap-2">
					{#each items as entry}
						<li class="text-xs">
							<div class="text-gray-400">{fromNs(entry.period_end)}</div>
							<div class="text-gray-700 dark:text-gray-300">{entry.summary}</div>
						</li>
					{:else}
						<li class="text-xs text-gray-500">{$i18n.t('No updates in the last 30 days')}</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</div>
</div>
