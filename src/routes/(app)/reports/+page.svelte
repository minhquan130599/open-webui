<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { copyToClipboard } from '$lib/utils';
	import DOMPurify from 'dompurify';

	import { WEBUI_NAME, mobile, showSidebar } from '$lib/stores';
	import {
		createReportSchedule,
		deleteReportSchedule,
		deleteTelegramLink,
		getReceivingReports,
		getReportAccess,
		getReportChannels,
		getReportSchedules,
		getTelegramLinkStatus,
		getTelegramLinks,
		newTelegramLink,
		previewReport,
		sendTestReport,
		unsubscribeReport,
		updateReportSchedule,
		type ReceivingReport,
		type ReportAccess,
		type ReportConfig,
		type ReportPreview,
		type ReportSchedule,
		type TelegramLink
	} from '$lib/apis/reports';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';

	const i18n: any = getContext('i18n');

	// Preserve dynamically translated labels for i18n extraction.
	// t('Daily') t('Weekly') t('Monthly') t('Custom') t('Overview of each channel') t('Overdue and due soon')
	// t('Completed in the period') t('Blockers and risks') t('By person in charge') t('Decisions in the period')
	const FREQUENCIES = [
		['daily', 'Daily'],
		['weekly', 'Weekly'],
		['monthly', 'Monthly'],
		['custom', 'Custom']
	];
	const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
	const SECTIONS = [
		['overview', 'Overview of each channel'],
		['due', 'Overdue and due soon'],
		['done', 'Completed in the period'],
		['blockers', 'Blockers and risks'],
		['people', 'By person in charge'],
		['decisions', 'Decisions in the period']
	];
	const SUGGESTIONS = [
		'Viết ngắn gọn cho lãnh đạo, tối đa 8 gạch đầu dòng, nêu 3 rủi ro lớn nhất và việc cần quyết định.',
		'Nhấn mạnh các việc trễ hạn và vướng mắc, ghi rõ ai cần gỡ.',
		'Tóm tắt theo từng người phụ trách: việc đã xong, việc trễ, việc tuần tới.'
	];

	let access: ReportAccess | null = null;
	let loading = true;
	let schedules: ReportSchedule[] = [];
	let receiving: ReceivingReport[] = [];
	let channels: { id: string; name: string }[] = [];
	let links: TelegramLink[] = [];

	type Form = { id: string | null; name: string; enabled: boolean; config: ReportConfig };
	let form: Form | null = null;
	let saving = false;
	let busy = '';
	let channelFilter = '';
	let recipientInput = '';
	let customTimes = '';

	let preview: ReportPreview | null = null;
	let showPreview = false;
	let previewTab: 'email' | 'telegram' = 'email';

	let linking: { id: string; url: string; group_command: string; allow_groups: boolean } | null = null;
	// Linking a Telegram group is hidden for now; set to true to show the "/link <code>" part again.
	const SHOW_TELEGRAM_GROUP_LINK = false;
	let linkTimer: ReturnType<typeof setInterval> | null = null;

	let confirmDelete = false;
	let confirmUnsubscribe: ReceivingReport | null = null;
	let showUnsubscribe = false;

	const blankConfig = (): ReportConfig => ({
		scope: 'all',
		channel_ids: [],
		frequency: 'weekly',
		weekdays: [0],
		workdays_only: true,
		month_day: 1,
		times: ['08:00'],
		period: 'since_last',
		sections: ['overview', 'due', 'done', 'blockers'],
		prompt: '',
		skip_unchanged: true,
		email: { enabled: !!access?.email_enabled, self: true, to: [] },
		telegram: { enabled: false, link_ids: [] }
	});

	const load = async () => {
		const token = localStorage.token;
		try {
			access = await getReportAccess(token);
		} catch {
			access = null;
		}
		if (access?.enabled) {
			const tasks: Promise<any>[] = [getReceivingReports(token).then((r) => (receiving = r)).catch(() => [])];
			if (access.can_create) {
				tasks.push(getReportSchedules(token).then((r) => (schedules = r)));
				tasks.push(getReportChannels(token).then((r) => (channels = r)));
				if (access.telegram_enabled) tasks.push(getTelegramLinks(token).then((r) => (links = r)));
			}
			await Promise.all(tasks).catch((e) => toast.error(e?.detail ?? String(e)));
		}
		loading = false;
	};

	onMount(load);
	onDestroy(() => linkTimer && clearInterval(linkTimer));

	const edit = (schedule: ReportSchedule | null) => {
		form = schedule
			? {
					id: schedule.id,
					name: schedule.name,
					enabled: schedule.enabled,
					config: JSON.parse(JSON.stringify({ ...blankConfig(), ...schedule.config }))
				}
			: { id: null, name: $i18n.t('Weekly progress report'), enabled: true, config: blankConfig() };
		customTimes = form.config.times.join(', ');
		recipientInput = '';
		channelFilter = '';
	};

	const payload = () => {
		if (!form) return null;
		const config = { ...form.config };
		if (config.frequency === 'custom') {
			config.times = customTimes
				.split(/[,;\s]+/)
				.map((t) => t.trim())
				.filter(Boolean);
		}
		addRecipient();
		return { name: form.name, enabled: form.enabled, config };
	};

	const save = async () => {
		const body = payload();
		if (!form || !body) return;
		saving = true;
		try {
			const saved = form.id
				? await updateReportSchedule(localStorage.token, form.id, body)
				: await createReportSchedule(localStorage.token, body);
			schedules = form.id ? schedules.map((s) => (s.id === saved.id ? saved : s)) : [...schedules, saved];
			form.id = saved.id;
			toast.success($i18n.t('Report schedule saved'));
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		} finally {
			saving = false;
		}
	};

	const remove = async () => {
		if (!form?.id) return;
		try {
			await deleteReportSchedule(localStorage.token, form.id);
			schedules = schedules.filter((s) => s.id !== form?.id);
			form = null;
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		}
	};

	const runPreview = async () => {
		const body = payload();
		if (!body) return;
		busy = 'preview';
		try {
			preview = await previewReport(localStorage.token, body);
			previewTab = 'email';
			showPreview = true;
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		} finally {
			busy = '';
		}
	};

	const runTest = async () => {
		const body = payload();
		if (!body) return;
		busy = 'test';
		try {
			const result = await sendTestReport(localStorage.token, body);
			if (result.sent.length) toast.success($i18n.t('Sent to: {{list}}', { list: result.sent.join(', ') }));
			for (const error of result.errors) toast.error(error);
			if (!result.sent.length && !result.errors.length) toast.error($i18n.t('Nothing was sent'));
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		} finally {
			busy = '';
		}
	};

	const toggleDay = (day: number) => {
		if (!form) return;
		const days = form.config.weekdays;
		form.config.weekdays = days.includes(day) ? days.filter((d) => d !== day) : [...days, day].sort();
	};

	const toggleChannel = (id: string) => {
		if (!form) return;
		const ids = form.config.channel_ids;
		form.config.channel_ids = ids.includes(id) ? ids.filter((c) => c !== id) : [...ids, id];
	};

	const toggleSection = (key: string) => {
		if (!form) return;
		const list = form.config.sections as string[];
		form.config.sections = (list.includes(key) ? list.filter((s) => s !== key) : [...list, key]) as any;
	};

	const addRecipient = () => {
		if (!form) return;
		const parts = recipientInput
			.split(/[,;\s]+/)
			.map((a) => a.trim().toLowerCase())
			.filter(Boolean);
		if (parts.length) {
			form.config.email.to = [...new Set([...form.config.email.to, ...parts])];
			recipientInput = '';
		}
	};

	const toggleLink = (id: string) => {
		if (!form) return;
		const ids = form.config.telegram.link_ids;
		form.config.telegram.link_ids = ids.includes(id) ? ids.filter((l) => l !== id) : [...ids, id];
	};

	const startLink = async () => {
		try {
			const result = await newTelegramLink(localStorage.token);
			linking = result;
			if (linkTimer) clearInterval(linkTimer);
			linkTimer = setInterval(async () => {
				if (!linking) return;
				const status = await getTelegramLinkStatus(localStorage.token, linking.id).catch(() => null);
				if (status?.linked) {
					const id = linking.id;
					linking = null;
					if (linkTimer) clearInterval(linkTimer);
					links = await getTelegramLinks(localStorage.token);
					if (form && !form.config.telegram.link_ids.includes(id)) {
						form.config.telegram.link_ids = [...form.config.telegram.link_ids, id];
						form.config.telegram.enabled = true;
					}
					toast.success($i18n.t('Telegram linked: {{name}}', { name: status.chat_title ?? '' }));
				}
			}, 3000);
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		}
	};

	const unlink = async (link: TelegramLink) => {
		await deleteTelegramLink(localStorage.token, link.id).catch(() => null);
		links = links.filter((l) => l.id !== link.id);
		if (form) form.config.telegram.link_ids = form.config.telegram.link_ids.filter((i) => i !== link.id);
	};

	const unsubscribe = async (report: ReceivingReport) => {
		try {
			await unsubscribeReport(localStorage.token, report.id);
			receiving = receiving.filter((r) => r.id !== report.id);
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		}
	};

	const formatTime = (ns: number | null) =>
		ns
			? new Date(ns / 1e6).toLocaleString('vi-VN', {
					weekday: 'short',
					day: '2-digit',
					month: '2-digit',
					hour: '2-digit',
					minute: '2-digit'
				})
			: '—';

	// copyToClipboard falls back to execCommand where navigator.clipboard is missing (the app served over plain HTTP).
	const copy = async (text: string) => {
		if (await copyToClipboard(text)) toast.success($i18n.t('Copied'));
		else toast.error($i18n.t('Could not copy to clipboard. Please try again.'));
	};

	$: filteredChannels = channels.filter((c) =>
		c.name.toLowerCase().includes(channelFilter.trim().toLowerCase())
	);
	$: channelName = (id: string) => channels.find((c) => c.id === id)?.name ?? id;
	$: telegramHtml = preview ? DOMPurify.sanitize(preview.telegram.replace(/\n/g, '<br>')) : '';
</script>

<svelte:head>
	<title>{$i18n.t('Scheduled reports')} / {$WEBUI_NAME}</title>
</svelte:head>

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
							<div class="self-center p-1.5"><SidebarIcon className="size-4" /></div>
						</button>
					</Tooltip>
				</div>
			{/if}
			<div class="flex w-full items-center justify-between gap-2 py-1 px-1">
				<div class="text-sm text-gray-500 dark:text-gray-400 truncate">
					{$WEBUI_NAME} /
					<span class="text-gray-900 dark:text-gray-100">{$i18n.t('Scheduled reports')}</span>
				</div>
				{#if access?.can_create}
					<button
						type="button"
						class="shrink-0 rounded-xl bg-black px-3 py-1.5 text-xs font-medium text-white transition hover:bg-gray-900 disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-gray-100"
						disabled={schedules.length >= (access?.max_schedules ?? 0)}
						on:click={() => edit(null)}
					>
						+ {$i18n.t('New report schedule')}
					</button>
				{/if}
			</div>
		</div>
	</nav>

	<div class="flex-1 overflow-y-auto">
		<div class="mx-auto flex max-w-6xl flex-col gap-5 px-4 pb-16 md:px-6">
			{#if loading}
				<div class="mt-16 flex justify-center"><Spinner className="size-5" /></div>
			{:else if !access?.enabled}
				<div class="mt-10 text-center text-sm text-gray-500 dark:text-gray-400">
					{$i18n.t('Scheduled reports are turned off by the administrator.')}
				</div>
			{:else}
				<p class="max-w-3xl text-sm leading-relaxed text-gray-600 dark:text-gray-400">
					{$i18n.t(
						'The AI summarizes the progress of your project channels on a schedule and sends it by email or Telegram. The figures always come from the progress tables; the AI only writes the comments.'
					)}
				</p>

				{#if !access.can_create}
					<div
						class="rounded-xl border border-gray-100 bg-gray-50 p-4 text-sm text-gray-600 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300"
					>
						{$i18n.t(
							'You cannot create scheduled reports. Ask a channel owner to add you to the recipients of their report.'
						)}
					</div>
				{:else}
					<div class="flex flex-wrap items-start gap-5">
						<!-- schedules -->
						<section class="flex w-full flex-col gap-2 md:w-80 md:shrink-0" aria-label={$i18n.t('Your report schedules')}>
							<div class="text-xs font-medium uppercase tracking-wide text-gray-500">
								{$i18n.t('Your report schedules')} ({schedules.length}/{access.max_schedules})
							</div>
							{#each schedules as schedule (schedule.id)}
								<button
									type="button"
									class="flex w-full flex-col gap-1 rounded-xl border px-3.5 py-3 text-left text-xs transition {form?.id ===
									schedule.id
										? 'border-blue-500 bg-blue-50/60 dark:bg-blue-950/30'
										: 'border-gray-100 bg-white hover:border-gray-200 dark:border-gray-800 dark:bg-gray-900'}"
									on:click={() => edit(schedule)}
								>
									<span class="flex w-full items-center justify-between gap-2">
										<span class="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{schedule.name}</span>
										<span
											class="shrink-0 font-medium {schedule.enabled
												? 'text-green-700 dark:text-green-400'
												: 'text-amber-700 dark:text-amber-400'}"
										>
											{schedule.enabled ? $i18n.t('On') : $i18n.t('Paused')}
										</span>
									</span>
									<span class="text-gray-600 dark:text-gray-300">{schedule.when}</span>
									<span class="text-gray-500">
										{schedule.config.scope === 'all'
											? $i18n.t('All my project channels')
											: $i18n.t('{{count}} channels', { count: schedule.config.channel_ids.length })}
										·
										{[schedule.config.email.enabled ? 'Email' : '', schedule.config.telegram.enabled ? 'Telegram' : '']
											.filter(Boolean)
											.join(', ')}
									</span>
									<span class="text-gray-500">
										{$i18n.t('Next')}: {schedule.enabled ? formatTime(schedule.next_run_at) : '—'}
									</span>
									{#if schedule.last_status === 'error'}
										<span class="text-red-600 dark:text-red-400" title={schedule.last_error ?? ''}>
											{$i18n.t('Last send failed')}: {schedule.last_error}
										</span>
									{:else if schedule.last_run_at}
										<span class="text-gray-400">
											{$i18n.t('Last')}: {formatTime(schedule.last_run_at)}
											{schedule.last_status === 'skipped' ? `(${$i18n.t('no change, skipped')})` : ''}
										</span>
									{/if}
								</button>
							{:else}
								<div class="rounded-xl border border-dashed border-gray-200 p-4 text-xs text-gray-500 dark:border-gray-700">
									{$i18n.t('No report schedule yet. Create one to receive a progress summary automatically.')}
								</div>
							{/each}
						</section>

						<!-- editor -->
						{#if form}
							<section
								class="min-w-0 flex-1 rounded-2xl border border-gray-100 bg-white dark:border-gray-800 dark:bg-gray-900"
								aria-label={$i18n.t('Edit report schedule')}
							>
								<div class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800">
									<div class="flex min-w-0 flex-1 flex-col gap-0.5">
										<label for="report-name" class="text-xs text-gray-500">{$i18n.t('Report name')}</label>
										<input
											id="report-name"
											class="w-full bg-transparent text-base font-semibold outline-none"
											bind:value={form.name}
										/>
									</div>
									<div class="flex items-center gap-2 text-sm">
										<span class="text-gray-600 dark:text-gray-300">{form.enabled ? $i18n.t('On') : $i18n.t('Paused')}</span>
										<Switch bind:state={form.enabled} ariaLabel={$i18n.t('Turn the schedule on or off')} />
									</div>
								</div>

								<!-- 1. channels -->
								<div class="flex flex-col gap-2.5 border-b border-gray-50 px-5 py-4 dark:border-gray-850">
									<h2 class="text-sm font-semibold">1. {$i18n.t('Channels in the report')}</h2>
									<label class="flex cursor-pointer items-start gap-2.5 text-sm">
										<input type="radio" class="mt-1" bind:group={form.config.scope} value="all" />
										<span>
											<span class="font-medium">
												{access.owners_only ? $i18n.t('All channels I run') : $i18n.t('All my project channels')}
											</span>
											<span class="block text-xs text-gray-500">
												{$i18n.t('Channels joined later are included automatically ({{count}} now).', {
													count: channels.length
												})}
											</span>
										</span>
									</label>
									<label class="flex cursor-pointer items-start gap-2.5 text-sm">
										<input type="radio" class="mt-1" bind:group={form.config.scope} value="pick" />
										<span class="font-medium">{$i18n.t('Only the chosen channels')}</span>
									</label>
									{#if form.config.scope === 'pick'}
										<div class="ml-6 flex flex-col gap-2 rounded-xl border border-gray-200 p-2.5 dark:border-gray-700">
											{#if form.config.channel_ids.length}
												<div class="flex flex-wrap gap-1.5">
													{#each form.config.channel_ids as id}
														<span class="inline-flex items-center gap-1 rounded-lg bg-gray-100 py-0.5 pl-2 pr-1 text-xs dark:bg-gray-800">
															#{channelName(id)}
															<button
																type="button"
																class="rounded px-1 text-gray-500 hover:text-gray-900 dark:hover:text-white"
																aria-label={$i18n.t('Remove channel')}
																on:click={() => toggleChannel(id)}>×</button
															>
														</span>
													{/each}
												</div>
											{/if}
											<input
												class="w-full rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs outline-none dark:bg-gray-850"
												placeholder={$i18n.t('Search channels…')}
												aria-label={$i18n.t('Search channels…')}
												bind:value={channelFilter}
											/>
											<div class="max-h-44 overflow-y-auto">
												{#each filteredChannels as channel (channel.id)}
													<label class="flex cursor-pointer items-center gap-2 rounded-lg px-1.5 py-1 text-xs hover:bg-gray-50 dark:hover:bg-gray-850">
														<input
															type="checkbox"
															checked={form.config.channel_ids.includes(channel.id)}
															on:change={() => toggleChannel(channel.id)}
														/>
														#{channel.name}
													</label>
												{:else}
													<div class="px-1.5 py-1 text-xs text-gray-500">{$i18n.t('No channel found')}</div>
												{/each}
											</div>
										</div>
									{/if}
								</div>

								<!-- 2. schedule -->
								<div class="flex flex-col gap-3 border-b border-gray-50 px-5 py-4 dark:border-gray-850">
									<h2 class="text-sm font-semibold">2. {$i18n.t('When to send')}</h2>
									<div class="flex w-fit flex-wrap gap-1 rounded-xl bg-gray-100 p-1 dark:bg-gray-850" role="group" aria-label={$i18n.t('Frequency')}>
										{#each FREQUENCIES as [value, label]}
											<button
												type="button"
												class="rounded-lg px-3 py-1.5 text-xs transition {form.config.frequency === value
													? 'bg-white font-medium shadow-sm dark:bg-gray-800'
													: 'text-gray-600 dark:text-gray-400'}"
												aria-pressed={form.config.frequency === value}
												on:click={() => form && (form.config.frequency = value as any)}>{$i18n.t(label)}</button
											>
										{/each}
									</div>

									{#if form.config.frequency === 'weekly' || form.config.frequency === 'custom'}
										<div class="flex flex-wrap gap-1.5" role="group" aria-label={$i18n.t('Days of the week')}>
											{#each WEEKDAYS as day, index}
												<button
													type="button"
													class="min-w-11 rounded-lg border px-2 py-1.5 text-xs transition {form.config.weekdays.includes(index)
														? 'border-black bg-black font-medium text-white dark:border-white dark:bg-white dark:text-black'
														: 'border-gray-200 dark:border-gray-700'}"
													aria-pressed={form.config.weekdays.includes(index)}
													on:click={() => toggleDay(index)}>{$i18n.t(day)}</button
												>
											{/each}
										</div>
									{/if}
									{#if form.config.frequency === 'daily'}
										<label class="flex items-center gap-2 text-xs">
											<input type="checkbox" bind:checked={form.config.workdays_only} />
											{$i18n.t('Working days only (Mon–Fri)')}
										</label>
									{/if}
									{#if form.config.frequency === 'monthly'}
										<label class="flex items-center gap-2 text-xs">
											{$i18n.t('Day')}
											<select class="rounded-lg border border-gray-200 bg-transparent px-2 py-1 dark:border-gray-700" bind:value={form.config.month_day}>
												{#each Array.from({ length: 28 }, (_, i) => i + 1) as day}
													<option value={day}>{day}</option>
												{/each}
												<option value={-1}>{$i18n.t('Last day of the month')}</option>
											</select>
										</label>
									{/if}

									<div class="flex flex-wrap items-end gap-4">
										{#if form.config.frequency === 'custom'}
											<label class="flex flex-col gap-1 text-xs">
												{$i18n.t('Times of day, separated by commas')}
												<input class="w-56 rounded-lg border border-gray-200 bg-transparent px-2.5 py-1.5 text-sm dark:border-gray-700" bind:value={customTimes} placeholder="08:00, 17:30" />
											</label>
										{:else}
											<label class="flex flex-col gap-1 text-xs">
												{$i18n.t('Time')}
												<input type="time" class="rounded-lg border border-gray-200 bg-transparent px-2.5 py-1.5 text-sm dark:border-gray-700" bind:value={form.config.times[0]} />
											</label>
										{/if}
										<span class="pb-2 text-xs text-gray-500">{$i18n.t('Vietnam time (GMT+7)')}</span>
										<label class="flex items-center gap-2 pb-2 text-xs">
											<input type="checkbox" bind:checked={form.config.skip_unchanged} />
											{$i18n.t('Skip when nothing changed')}
										</label>
									</div>
								</div>

								<!-- 3. content -->
								<div class="flex flex-col gap-3 border-b border-gray-50 px-5 py-4 dark:border-gray-850">
									<h2 class="text-sm font-semibold">3. {$i18n.t('Report content')}</h2>
									<label class="flex flex-wrap items-center gap-2 text-xs">
										{$i18n.t('Period')}
										<select class="rounded-lg border border-gray-200 bg-transparent px-2 py-1 dark:border-gray-700" bind:value={form.config.period}>
											<option value="since_last">{$i18n.t('Since the last report')}</option>
											<option value="7d">{$i18n.t('Last 7 days')}</option>
											<option value="last_month">{$i18n.t('Last month')}</option>
										</select>
									</label>
									<div class="grid grid-cols-1 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-2">
										{#each SECTIONS as [key, label]}
											<label class="flex items-center gap-2">
												<input type="checkbox" checked={(form.config.sections as string[]).includes(key)} on:change={() => toggleSection(key)} />
												{$i18n.t(label)}
											</label>
										{/each}
									</div>
									{#if access.allow_prompt}
										<div class="flex flex-col gap-1.5">
											<label for="report-prompt" class="text-xs font-medium">
												{$i18n.t('Your request to the AI')}
												<span class="font-normal text-gray-500">({$i18n.t('optional')})</span>
											</label>
											<textarea
												id="report-prompt"
												rows="4"
												class="w-full resize-y rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-700"
												placeholder={access.default_prompt}
												bind:value={form.config.prompt}
											></textarea>
											<div class="flex flex-wrap items-center gap-1.5">
												<span class="text-xs text-gray-500">{$i18n.t('Suggestions')}:</span>
												{#each SUGGESTIONS as text}
													<button
														type="button"
														class="rounded-full border border-gray-200 px-2.5 py-1 text-xs text-gray-600 transition hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-850"
														on:click={() => form && (form.config.prompt = text)}>{text.split(',')[0]}</button
													>
												{/each}
											</div>
											<p class="text-xs text-gray-500">
												{$i18n.t('Leave empty to use the default report (shown greyed out above).')}
											</p>
										</div>
									{/if}
								</div>

								<!-- 4. delivery -->
								<div class="flex flex-col gap-3 border-b border-gray-50 px-5 py-4 dark:border-gray-850">
									<h2 class="text-sm font-semibold">4. {$i18n.t('Send by')}</h2>
									<div class="flex flex-wrap gap-3">
										{#if access.email_enabled}
											<div class="flex min-w-64 flex-1 flex-col gap-2.5 rounded-xl border border-gray-100 p-3.5 dark:border-gray-800">
												<div class="flex items-center justify-between">
													<span class="text-sm font-medium">Email</span>
													<Switch bind:state={form.config.email.enabled} ariaLabel={$i18n.t('Send by email')} />
												</div>
												{#if form.config.email.enabled}
													<label class="flex items-center gap-2 text-xs">
														<input type="checkbox" bind:checked={form.config.email.self} />
														{$i18n.t('Send to me ({{email}})', { email: access.email })}
													</label>
													<div class="text-xs text-gray-600 dark:text-gray-300">{$i18n.t('Other recipients')}</div>
													<div class="flex flex-wrap gap-1.5 rounded-lg border border-gray-200 p-1.5 dark:border-gray-700">
														{#each form.config.email.to as address}
															<span class="inline-flex items-center gap-1 rounded-md bg-gray-100 py-0.5 pl-2 pr-1 text-xs dark:bg-gray-800">
																{address}
																<button
																	type="button"
																	class="px-1 text-gray-500 hover:text-gray-900 dark:hover:text-white"
																	aria-label={$i18n.t('Remove recipient')}
																	on:click={() => form && (form.config.email.to = form.config.email.to.filter((a) => a !== address))}>×</button
																>
															</span>
														{/each}
														<input
															class="min-w-32 flex-1 bg-transparent px-1 text-xs outline-none"
															placeholder={$i18n.t('Add email, press Enter')}
															aria-label={$i18n.t('Add email, press Enter')}
															bind:value={recipientInput}
															on:keydown={(e) => {
																if (e.key === 'Enter' || e.key === ',') {
																	e.preventDefault();
																	addRecipient();
																}
															}}
															on:blur={addRecipient}
														/>
													</div>
													{#if !access.allow_outside}
														<p class="text-xs text-gray-500">{$i18n.t('Only members of the channels in the report can be added.')}</p>
													{/if}
												{/if}
											</div>
										{/if}
										{#if access.telegram_enabled}
											<div class="flex min-w-64 flex-1 flex-col gap-2.5 rounded-xl border border-gray-100 p-3.5 dark:border-gray-800">
												<div class="flex items-center justify-between">
													<span class="text-sm font-medium">Telegram</span>
													<Switch bind:state={form.config.telegram.enabled} ariaLabel={$i18n.t('Send by Telegram')} />
												</div>
												{#if form.config.telegram.enabled}
													{#each links as link (link.id)}
														<div class="flex items-center justify-between gap-2 text-xs">
															<label class="flex items-center gap-2">
																<input
																	type="checkbox"
																	checked={form.config.telegram.link_ids.includes(link.id)}
																	on:change={() => toggleLink(link.id)}
																/>
																{link.chat_type === 'private' ? $i18n.t('Private chat') : $i18n.t('Group')}: {link.chat_title}
															</label>
															<button type="button" class="text-gray-500 hover:text-red-600" on:click={() => unlink(link)}>{$i18n.t('Unlink')}</button>
														</div>
													{:else}
														<p class="text-xs text-gray-500">{$i18n.t('No Telegram chat linked yet.')}</p>
													{/each}
													<button
														type="button"
														class="w-fit rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs transition hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-850"
														on:click={startLink}
													>
														{$i18n.t('Link a Telegram chat')}
													</button>
												{/if}
											</div>
										{/if}
										{#if !access.email_enabled && !access.telegram_enabled}
											<p class="text-xs text-gray-500">{$i18n.t('The administrator has not enabled email or Telegram yet.')}</p>
										{/if}
									</div>
								</div>

								<div class="flex flex-wrap items-center justify-between gap-2 px-5 py-4">
									{#if form.id}
										<button type="button" class="rounded-lg px-2.5 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30" on:click={() => (confirmDelete = true)}>
											{$i18n.t('Delete schedule')}
										</button>
									{:else}
										<span></span>
									{/if}
									<div class="flex flex-wrap gap-2">
										<button type="button" class="rounded-xl border border-gray-200 px-3.5 py-2 text-sm font-medium transition hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-850" disabled={!!busy} on:click={runPreview}>
											{busy === 'preview' ? $i18n.t('Writing the report…') : $i18n.t('Preview')}
										</button>
										<button type="button" class="rounded-xl border border-gray-200 px-3.5 py-2 text-sm font-medium transition hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-850" disabled={!!busy} on:click={runTest}>
											{busy === 'test' ? $i18n.t('Sending…') : $i18n.t('Send a test to me')}
										</button>
										<button type="button" class="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white transition hover:bg-gray-900 disabled:opacity-50 dark:bg-white dark:text-black" disabled={saving} on:click={save}>
											{saving ? $i18n.t('Saving…') : $i18n.t('Save schedule')}
										</button>
									</div>
								</div>
							</section>
						{:else if schedules.length}
							<div class="flex-1 self-center p-6 text-center text-sm text-gray-500">{$i18n.t('Choose a schedule to edit it.')}</div>
						{/if}
					</div>
				{/if}

				{#if receiving.length}
					<section class="flex max-w-3xl flex-col gap-2" aria-label={$i18n.t('Reports you receive')}>
						<h2 class="text-sm font-semibold">{$i18n.t('Reports you receive')} ({receiving.length})</h2>
						{#each receiving as report (report.id)}
							<div class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-100 px-4 py-3 dark:border-gray-800">
								<div class="flex flex-col gap-0.5 text-xs">
									<span class="text-sm font-medium">{report.name}</span>
									<span class="text-gray-600 dark:text-gray-300">{report.when}</span>
									<span class="text-gray-500">{$i18n.t('Created by {{name}}', { name: report.owner })}</span>
								</div>
								<button
									type="button"
									class="rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-red-600 dark:border-gray-700"
									on:click={() => {
										confirmUnsubscribe = report;
										showUnsubscribe = true;
									}}>{$i18n.t('Stop receiving')}</button
								>
							</div>
						{/each}
					</section>
				{/if}
			{/if}
		</div>
	</div>
</div>

<Modal bind:show={showPreview} size="xl">
	{#if preview}
		<div class="flex flex-col gap-3 p-4">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<div class="min-w-0">
					<div class="truncate text-base font-semibold">{preview.subject}</div>
					<div class="text-xs text-gray-500">
						{$i18n.t('{{channels}} channels · {{open}} open · {{overdue}} overdue · {{done}} done in the period', {
							channels: preview.channels,
							open: preview.totals.open,
							overdue: preview.totals.overdue,
							done: preview.totals.done
						})}
					</div>
				</div>
				<div class="flex gap-1 rounded-xl bg-gray-100 p-1 text-xs dark:bg-gray-850" role="group" aria-label={$i18n.t('Preview')}>
					{#each [['email', 'Email'], ['telegram', 'Telegram']] as [value, label]}
						<button
							type="button"
							class="rounded-lg px-3 py-1 {previewTab === value ? 'bg-white font-medium shadow-sm dark:bg-gray-800' : ''}"
							aria-pressed={previewTab === value}
							on:click={() => (previewTab = value as any)}>{label}</button
						>
					{/each}
				</div>
			</div>
			{#if previewTab === 'email'}
				<iframe title={$i18n.t('Email preview')} class="h-[65vh] w-full rounded-xl border border-gray-100 bg-white dark:border-gray-800" sandbox="" srcdoc={preview.html}></iframe>
			{:else}
				<div class="h-[65vh] overflow-y-auto rounded-xl bg-[#dfe7ee] p-4 dark:bg-gray-850">
					<div class="max-w-md rounded-2xl rounded-bl-sm bg-white p-3 text-sm leading-relaxed text-gray-900 dark:bg-gray-900 dark:text-gray-100">
						{@html telegramHtml}
					</div>
				</div>
			{/if}
			<div class="flex justify-end">
				<button type="button" class="rounded-xl border border-gray-200 px-3.5 py-1.5 text-sm dark:border-gray-700" on:click={() => (showPreview = false)}>{$i18n.t('Close')}</button>
			</div>
		</div>
	{/if}
</Modal>

<Modal show={!!linking} size="sm" onRequestClose={() => (linking = null)}>
	{#if linking}
		<div class="flex flex-col gap-3 p-5 text-sm">
			<div class="text-base font-semibold">{$i18n.t('Link a Telegram chat')}</div>
			<div class="flex flex-col gap-1.5">
				<span class="font-medium">{$i18n.t('Private chat with the bot')}</span>
				{#if linking.url}
					<a class="w-fit rounded-xl bg-[#229ED9] px-3.5 py-2 font-medium text-white" href={linking.url} target="_blank" rel="noopener">{$i18n.t('Open Telegram')}</a>
				{/if}
				<span class="text-xs text-gray-500">{$i18n.t('Telegram opens the bot; press Start.')}</span>
			</div>
			{#if SHOW_TELEGRAM_GROUP_LINK && linking.allow_groups}
				<div class="flex flex-col gap-1.5">
					<span class="font-medium">{$i18n.t('A Telegram group')}</span>
					<span class="text-xs text-gray-500">{$i18n.t('Add the bot to the group, then send this message in the group:')}</span>
					<button type="button" title={$i18n.t('Click to copy')} class="flex w-fit items-center gap-2 rounded-lg bg-gray-100 px-2.5 py-1.5 font-mono text-xs select-all hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800" on:click={() => linking && copy(linking.group_command)}>
						{linking.group_command}
						<span class="font-sans text-gray-500">{$i18n.t('Copy')}</span>
					</button>
				</div>
			{/if}
			<div class="flex items-center gap-2 text-xs text-gray-500"><Spinner className="size-3" />{$i18n.t('Waiting for the chat to be linked…')}</div>
			<div class="flex justify-end">
				<button type="button" class="rounded-xl border border-gray-200 px-3.5 py-1.5 dark:border-gray-700" on:click={() => (linking = null)}>{$i18n.t('Close')}</button>
			</div>
		</div>
	{/if}
</Modal>

<ConfirmDialog
	bind:show={confirmDelete}
	title={$i18n.t('Delete schedule')}
	message={$i18n.t('Delete the report schedule "{{name}}"? Nothing more will be sent.', { name: form?.name ?? '' })}
	on:confirm={remove}
/>

<ConfirmDialog
	bind:show={showUnsubscribe}
	title={$i18n.t('Stop receiving')}
	message={$i18n.t('Stop receiving "{{name}}"?', { name: confirmUnsubscribe?.name ?? '' })}
	on:confirm={() => confirmUnsubscribe && unsubscribe(confirmUnsubscribe)}
/>
