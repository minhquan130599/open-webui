<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { slide } from 'svelte/transition';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import dayjs from 'dayjs';

	import { WEBUI_NAME, config, mobile, showSidebar, user } from '$lib/stores';
	import {
		archiveTrackerItems,
		getMyWork,
		getTeam,
		type MyWork,
		type MyWorkItem,
		type Team,
		type WorkBucket
	} from '$lib/apis/tracker';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import SearchIcon from '$lib/components/layout/Sidebar/icons/Search.svelte';

	const i18n: any = getContext('i18n');

	// Preserve dynamically translated labels for i18n extraction.
	// t('Overdue') t('Due today') t('Next 7 days') t('Later') t('No due date') t('Done, waiting to be closed')
	const BUCKETS: { key: WorkBucket; label: string; className: string }[] = [
		{ key: 'overdue', label: 'Overdue', className: 'text-red-600 dark:text-red-400' },
		{ key: 'today', label: 'Due today', className: 'text-amber-600 dark:text-amber-400' },
		{ key: 'week', label: 'Next 7 days', className: 'text-gray-900 dark:text-gray-100' },
		{ key: 'later', label: 'Later', className: 'text-gray-900 dark:text-gray-100' },
		{ key: 'no_due', label: 'No due date', className: 'text-gray-900 dark:text-gray-100' },
		{
			key: 'done',
			label: 'Done, waiting to be closed',
			className: 'text-green-700 dark:text-green-400'
		}
	];

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

	let tab: 'mine' | 'team' = 'mine';
	let work: MyWork | null = null;
	let team: Team | null = null;
	let canViewTeam = false;
	let memberId = '';
	let loading = true;
	let error = '';
	let query = '';
	let channelFilter = '';
	let showDone = false;
	let menuFor: string | null = null;
	let busy = false;
	// Item waiting for the cancel confirmation.
	let pendingCancel: MyWorkItem | null = null;
	let showConfirm = false;
	let timer: ReturnType<typeof setInterval> | null = null;

	const normalize = (text = '') =>
		text.toLocaleLowerCase('vi').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd');

	const loadWork = async (userId = '') => {
		loading = true;
		error = '';
		try {
			work = await getMyWork(localStorage.token, userId || undefined);
			if (!userId) canViewTeam = work.can_view_team;
		} catch (e: any) {
			error = e?.detail ?? String(e);
			work = null;
		} finally {
			loading = false;
		}
	};

	// Reload what is on screen without a spinner (after an action, on focus, periodically), so changes made
	// in a channel's progress panel show up here and the other way round.
	const reload = async () => {
		if (tab === 'team' && !memberId) {
			team = await getTeam(localStorage.token).catch(() => team);
		} else {
			work = await getMyWork(localStorage.token, memberId || undefined).catch(() => work);
		}
	};

	const archive = async (item: MyWorkItem, kind: 'dong' | 'huy') => {
		busy = true;
		menuFor = null;
		try {
			await archiveTrackerItems(localStorage.token, item.channel_id, [item.id], kind);
			toast.success(kind === 'dong' ? $i18n.t('Work item closed') : $i18n.t('Work item cancelled'));
			await reload();
		} catch (e: any) {
			toast.error(`${e?.detail ?? e}`);
		} finally {
			busy = false;
		}
	};

	const onVisible = () => document.visibilityState === 'visible' && reload();

	const loadTeam = async () => {
		loading = true;
		error = '';
		try {
			team = await getTeam(localStorage.token);
		} catch (e: any) {
			error = e?.detail ?? String(e);
		} finally {
			loading = false;
		}
	};

	const setUrl = (params: Record<string, string>) => {
		const url = new URL($page.url);
		url.search = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
		goto(url.pathname + url.search, { replaceState: true, keepFocus: true, noScroll: true });
	};

	const showMine = async () => {
		tab = 'mine';
		memberId = '';
		channelFilter = '';
		setUrl({});
		await loadWork();
	};

	const showTeam = async () => {
		tab = 'team';
		memberId = '';
		setUrl({ tab: 'team' });
		await loadTeam();
	};

	const openMember = async (id: string) => {
		tab = 'team';
		memberId = id;
		channelFilter = '';
		setUrl({ tab: 'team', user: id });
		await loadWork(id);
	};

	$: channels = work
		? [...new Map(work.items.map((i) => [i.channel_id, i.channel_name])).entries()].sort((a, b) =>
				a[1].localeCompare(b[1], 'vi')
			)
		: [];

	$: filtered = (work?.items ?? []).filter(
		(item) =>
			(!channelFilter || item.channel_id === channelFilter) &&
			(!query.trim() ||
				normalize(`${item.ten} ${item.channel_name} ${item.vuong_mac ?? ''}`).includes(
					normalize(query.trim())
				))
	);

	const byDue = (a: MyWorkItem, b: MyWorkItem) =>
		(a.han ?? '9999').localeCompare(b.han ?? '9999') || a.ten.localeCompare(b.ten, 'vi');

	$: sections = BUCKETS.map((bucket) => ({
		...bucket,
		items: filtered.filter((i) => i.bucket === bucket.key).sort(byDue)
	})).filter((s) => s.items.length > 0);

	$: openCount = filtered.filter((i) => i.bucket !== 'done').length;

	const dueText = (item: MyWorkItem) => {
		if (!item.han) return '';
		const date = dayjs(item.han).format('DD/MM/YYYY');
		const days = item.days_left;
		if (item.bucket === 'done' || days === null) return date;
		if (days < 0) return `${date} · ${$i18n.t('{{count}} days overdue', { count: -days })}`;
		if (days === 0) return `${date} · ${$i18n.t('today')}`;
		if (days === 1) return `${date} · ${$i18n.t('tomorrow')}`;
		return `${date} · ${$i18n.t('in {{count}} days', { count: days })}`;
	};

	$: memberName =
		memberId && work?.user?.id === memberId
			? work.user.name
			: (team?.people.find((p) => p.id === memberId)?.name ?? '');

	onDestroy(() => {
		if (timer) clearInterval(timer);
		document.removeEventListener('visibilitychange', onVisible);
		window.removeEventListener('focus', onVisible);
	});

	onMount(async () => {
		timer = setInterval(reload, 30_000);
		document.addEventListener('visibilitychange', onVisible);
		window.addEventListener('focus', onVisible);
		const params = $page.url.searchParams;
		if (params.get('tab') === 'team') {
			// Learn whether the viewer may see the team before opening it.
			await loadWork();
			if (!canViewTeam) return;
			tab = 'team';
			if (params.get('user')) {
				memberId = params.get('user') ?? '';
				await Promise.all([loadTeam(), loadWork(memberId)]);
			} else {
				await loadTeam();
			}
		} else {
			await loadWork();
		}
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
				{#if canViewTeam}
					<div
						class="flex shrink-0 gap-0.5 rounded-xl p-0.5 bg-gray-50 dark:bg-gray-850 border border-gray-100 dark:border-gray-800 text-xs"
						role="group"
						aria-label={$i18n.t('View')}
					>
						{#each [['mine', $i18n.t('Mine')], ['team', $i18n.t('Members')]] as [value, label]}
							<button
								type="button"
								class="px-2.5 py-1 rounded-lg transition {tab === value
									? 'bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-sm font-medium'
									: 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'}"
								aria-pressed={tab === value}
								on:click={() => (value === 'mine' ? showMine() : showTeam())}
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
		<div class="max-w-4xl mx-auto px-4 md:px-6 pb-16">
			{#if !($config?.features?.enable_project_tracker ?? false)}
				<div class="mt-10 text-center text-sm text-gray-500 dark:text-gray-400">
					{$i18n.t('Work tracking is not enabled on this server.')}
				</div>
			{:else if tab === 'team' && !memberId}
				<!-- Members of the channels the viewer owns (admins: every channel) -->
				<div class="mt-3 mb-4">
					<h1 class="text-xl font-semibold text-gray-900 dark:text-white">
						{$i18n.t('Members')}
					</h1>
					<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
						{$user?.role === 'admin'
							? $i18n.t('Work of everyone in the channels you are in.')
							: $i18n.t('Work of the members in the channels you own.')}
					</p>
				</div>
				{#if loading && !team}
					<div class="flex justify-center py-16"><Spinner className="size-5" /></div>
				{:else if error}
					<div class="text-sm text-red-600 dark:text-red-400">{error}</div>
				{:else if team}
					{#if team.unassigned > 0}
						<div
							class="mb-3 rounded-xl border border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-800 dark:text-amber-300"
						>
							{$i18n.t('{{count}} open work items have no owner yet.', {
								count: team.unassigned
							})}
						</div>
					{/if}
					{#if team.people.length === 0}
						<div class="py-10 text-center text-sm text-gray-500">{$i18n.t('No members')}</div>
					{:else}
						<ul class="flex flex-col gap-1.5">
							{#each team.people as person (person.id)}
								{@const open =
									person.counts.overdue +
									person.counts.today +
									person.counts.week +
									person.counts.later +
									person.counts.no_due}
								<li>
									<button
										type="button"
										class="w-full text-left rounded-xl border border-gray-100 dark:border-gray-800 px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-900 transition"
										on:click={() => openMember(person.id)}
									>
										<div class="flex items-center justify-between gap-3">
											<div class="min-w-0">
												<div class="font-medium text-sm text-gray-900 dark:text-gray-100 truncate">
													{person.name}
												</div>
												<div class="text-xs text-gray-500 truncate">{person.email}</div>
											</div>
											<div class="flex shrink-0 flex-wrap justify-end gap-1.5 text-[0.6875rem]">
												{#if person.counts.overdue}
													<span
														class="rounded-full px-2 py-0.5 bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300"
														>{$i18n.t('Overdue')}: {person.counts.overdue}</span
													>
												{/if}
												{#if person.counts.today}
													<span
														class="rounded-full px-2 py-0.5 bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
														>{$i18n.t('Due today')}: {person.counts.today}</span
													>
												{/if}
												<span
													class="rounded-full px-2 py-0.5 bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
													>{$i18n.t('Open')}: {open}</span
												>
												{#if person.counts.done}
													<span
														class="rounded-full px-2 py-0.5 bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300"
														>{$i18n.t('Done')}: {person.counts.done}</span
													>
												{/if}
											</div>
										</div>
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				{/if}
			{:else}
				<div class="mt-3 mb-4 flex flex-wrap items-end justify-between gap-3">
					<div class="min-w-0">
						{#if memberId}
							<button
								type="button"
								class="text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
								on:click={showTeam}>← {$i18n.t('Members')}</button
							>
						{/if}
						<h1 class="text-xl font-semibold text-gray-900 dark:text-white truncate">
							{memberId ? memberName || $i18n.t('Member') : $i18n.t('My work')}
						</h1>
						{#if work}
							<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
								{$i18n.t('{{count}} open work items', { count: openCount })}
								{#if memberId && $user?.role !== 'admin'}
									· {$i18n.t('in the channels you own')}
								{/if}
							</p>
						{/if}
					</div>
					{#if work && work.items.length > 0}
						<div class="flex flex-wrap items-center gap-2">
							<label
								class="flex items-center gap-1.5 rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-2.5"
							>
								<SearchIcon className="size-3.5 text-gray-400" strokeWidth="1.5" />
								<input
									bind:value={query}
									class="w-36 bg-transparent outline-none py-1.5 text-xs placeholder:text-gray-400"
									placeholder={$i18n.t('Search')}
									aria-label={$i18n.t('Search')}
								/>
							</label>
							{#if channels.length > 1}
								<select
									bind:value={channelFilter}
									class="rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-xs outline-none"
									aria-label={$i18n.t('Channel')}
								>
									<option value="">{$i18n.t('All channels')}</option>
									{#each channels as [id, name]}
										<option value={id}>{name}</option>
									{/each}
								</select>
							{/if}
						</div>
					{/if}
				</div>

				{#if loading && !work}
					<div class="flex justify-center py-16"><Spinner className="size-5" /></div>
				{:else if error}
					<div class="text-sm text-red-600 dark:text-red-400">{error}</div>
				{:else if work && sections.length === 0}
					<div class="py-16 text-center text-sm text-gray-500 dark:text-gray-400">
						{work.items.length === 0
							? $i18n.t('No work items are assigned yet.')
							: $i18n.t('No work items match the filter.')}
					</div>
				{:else}
					{#each sections as section (section.key)}
						{@const collapsedDone = section.key === 'done' && !showDone}
						<section class="mb-5">
							<button
								type="button"
								class="mb-2 flex items-center gap-2 text-sm font-medium {section.className}"
								on:click={() => section.key === 'done' && (showDone = !showDone)}
								disabled={section.key !== 'done'}
							>
								{$i18n.t(section.label)}
								<span class="text-xs font-normal text-gray-500">({section.items.length})</span>
								{#if section.key === 'done'}
									<span class="text-xs font-normal text-gray-500"
										>{showDone ? $i18n.t('Hide') : $i18n.t('Show')}</span
									>
								{/if}
							</button>
							{#if !collapsedDone}
								<ul class="flex flex-col gap-2">
									{#each section.items as item (item.channel_id + item.id)}
										<li
											class="rounded-xl border px-3 py-2.5 {item.bucket === 'overdue'
												? 'border-red-200 dark:border-red-900/60'
												: 'border-gray-100 dark:border-gray-800'}"
										>
											<div class="flex items-start justify-between gap-2">
												<div class="min-w-0">
													<div
														class="text-sm font-medium text-gray-900 dark:text-gray-100 break-words"
													>
														{item.ten}
													</div>
													<a
														href="/channels/{item.channel_id}"
														class="text-xs text-gray-500 hover:underline"># {item.channel_name}</a
													>
												</div>
												<div class="flex shrink-0 items-center gap-0.5">
													<span
														class="rounded-full px-2 py-0.5 text-[0.6875rem] {STATUS[
															item.trang_thai
														]?.className ?? STATUS.dang_lam.className}"
													>
														{$i18n.t(STATUS[item.trang_thai]?.label ?? 'In progress')}
													</span>
													{#if item.can_manage}
														{@const key = item.channel_id + item.id}
														<div class="relative">
															<button
																type="button"
																class="flex size-6 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-gray-800 dark:hover:text-white"
																aria-label={$i18n.t('Options')}
																aria-haspopup="menu"
																aria-expanded={menuFor === key}
																disabled={busy}
																on:click|stopPropagation={() =>
																	(menuFor = menuFor === key ? null : key)}
															>
																<EllipsisHorizontal className="size-4" />
															</button>
															{#if menuFor === key}
																<div
																	class="absolute right-0 top-7 z-20 flex min-w-40 flex-col rounded-xl border border-gray-100 bg-white p-1 shadow-lg dark:border-gray-800 dark:bg-gray-900"
																	role="menu"
																	transition:slide={{ duration: 120 }}
																>
																	<button
																		type="button"
																		role="menuitem"
																		class="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left text-xs text-green-700 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-950/40"
																		on:click|stopPropagation={() => archive(item, 'dong')}
																		><span aria-hidden="true">✓</span>{$i18n.t(
																			'Complete & close'
																		)}</button
																	>
																	<button
																		type="button"
																		role="menuitem"
																		class="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
																		on:click|stopPropagation={() => {
																			menuFor = null;
																			pendingCancel = item;
																			showConfirm = true;
																		}}
																		><span aria-hidden="true">✕</span>{$i18n.t(
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
												<div class="flex items-center gap-2 mt-1.5">
													<div
														class="h-1.5 flex-1 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden"
													>
														<div
															class="h-full rounded-full {item.trang_thai === 'bi_chan'
																? 'bg-red-500'
																: 'bg-blue-500'}"
															style="width: {item.tien_do}%"
														></div>
													</div>
													<span class="text-xs tabular-nums text-gray-500">{item.tien_do}%</span>
												</div>
											{/if}
											<div
												class="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-500 dark:text-gray-400"
											>
												{#if item.han}
													<span
														class={item.bucket === 'overdue'
															? 'text-red-600 dark:text-red-400'
															: item.bucket === 'today'
																? 'text-amber-600 dark:text-amber-400'
																: ''}
													>
														{$i18n.t('Due')}: {dueText(item)}
													</span>
												{/if}
												{#if item.phu_trach.length > 1}
													<span>{$i18n.t('Assignee')}: {item.phu_trach.join(', ')}</span>
												{/if}
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
						</section>
					{/each}
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
