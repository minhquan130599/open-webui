<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deleteAnyReportSchedule,
		getAllReportSchedules,
		getReportAdminSettings,
		runReportNow,
		saveReportAdminSettings,
		setReportScheduleEnabled,
		type ReportSchedule
	} from '$lib/apis/reports';
	import Switch from '$lib/components/common/Switch.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	export let saveHandler: () => void = () => {};

	let values: Record<string, any> | null = null;
	let schedules: ReportSchedule[] = [];
	let running = '';

	const inputClass =
		'w-full rounded-lg border border-gray-100 bg-transparent px-2.5 py-1.5 text-sm outline-none dark:border-gray-850';

	onMount(async () => {
		try {
			values = await getReportAdminSettings(localStorage.token);
			schedules = await getAllReportSchedules(localStorage.token);
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		}
	});

	const save = async () => {
		if (!values) return;
		const changes: Record<string, any> = {};
		for (const [key, value] of Object.entries(values)) {
			// The bot is configured in Admin settings → Telegram.
			if (!key.startsWith('reports.telegram.')) changes[key] = value;
		}
		try {
			values = await saveReportAdminSettings(localStorage.token, changes);
			saveHandler();
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		}
	};

	const toggle = async (schedule: ReportSchedule) => {
		await setReportScheduleEnabled(localStorage.token, schedule.id, !schedule.enabled).catch((e) =>
			toast.error(e?.detail ?? String(e))
		);
		schedules = await getAllReportSchedules(localStorage.token);
	};

	const runNow = async (schedule: ReportSchedule) => {
		running = schedule.id;
		try {
			const result: any = await runReportNow(localStorage.token, schedule.id);
			if (result.sent?.length) toast.success($i18n.t('Sent to: {{list}}', { list: result.sent.join(', ') }));
			for (const error of result.errors ?? []) toast.error(error);
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		} finally {
			running = '';
			schedules = await getAllReportSchedules(localStorage.token);
		}
	};

	const remove = async (schedule: ReportSchedule) => {
		if (!confirm($i18n.t('Delete the report schedule "{{name}}"? Nothing more will be sent.', { name: schedule.name }))) return;
		await deleteAnyReportSchedule(localStorage.token, schedule.id).catch((e) => toast.error(e?.detail ?? String(e)));
		schedules = schedules.filter((s) => s.id !== schedule.id);
	};

	const when = (ns: number | null) =>
		ns ? new Date(ns / 1e6).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={save}>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$i18n.t('settings.admin.reports.title')}</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if values}
			<AdminSettingSection first>
				<AdminSettingRow
					label={$i18n.t('Scheduled reports')}
					description={$i18n.t('Let users schedule the AI to summarize channel progress and send it by email or Telegram.')}
					let:labelId
				>
					<Switch ariaLabelledbyId={labelId} bind:state={values['reports.enable']} />
				</AdminSettingRow>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Who can create and edit scheduled reports')}>
				<div class="grid grid-cols-1 gap-2 sm:grid-cols-3">
					{#each [['all', 'All users', 'With the channels they are in.'], ['owners', 'Channel owners and admins', 'Owners can only choose the channels they run.'], ['admins', 'Admins only', 'Others only receive reports.']] as [value, label, hint]}
						<label class="flex cursor-pointer gap-2 rounded-xl border p-3 {values['reports.who'] === value ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-950/20' : 'border-gray-100 dark:border-gray-850'}">
							<input type="radio" class="mt-0.5" bind:group={values['reports.who']} {value} />
							<span>
								<span class="font-medium">{$i18n.t(label)}</span>
								<span class="block text-xs text-gray-500">{$i18n.t(hint)}</span>
							</span>
						</label>
					{/each}
				</div>
				<AdminSettingRow label={$i18n.t('Users without permission')}>
					<select class="rounded-lg border border-gray-100 bg-transparent px-2 py-1 text-xs dark:border-gray-850" bind:value={values['reports.hidden_mode']}>
						<option value="receiving">{$i18n.t('Only see the reports they receive (to stop them)')}</option>
						<option value="hidden">{$i18n.t('Hide the Scheduled reports page')}</option>
					</select>
				</AdminSettingRow>
				<AdminSettingRow label={$i18n.t('Allow a custom request to the AI')} let:labelId>
					<Switch ariaLabelledbyId={labelId} bind:state={values['reports.allow_prompt']} />
				</AdminSettingRow>
				<AdminSettingRow label={$i18n.t('Allow recipients who are not channel members')} let:labelId>
					<Switch ariaLabelledbyId={labelId} bind:state={values['reports.allow_outside']} />
				</AdminSettingRow>
			</AdminSettingSection>

			<AdminSettingSection title="Email">
				<AdminSettingRow
					label={$i18n.t('Send by email')}
					description={$i18n.t('Uses the reminder email settings (Microsoft 365 or SMTP).')}
					let:labelId
				>
					<Switch ariaLabelledbyId={labelId} bind:state={values['reports.email.enable']} />
				</AdminSettingRow>
				<AdminSettingRow label={$i18n.t("Only send to the company's domain (the sender's domain)")} let:labelId>
					<Switch ariaLabelledbyId={labelId} bind:state={values['reports.email.internal_only']} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('Several recipients')}
					description={$i18n.t('One email for all recipients uses far less of the mailbox sending limit (Microsoft 365: about 30 emails a minute).')}
				>
					<select class="rounded-lg border border-gray-100 bg-transparent px-2 py-1 text-xs dark:border-gray-850" bind:value={values['reports.email.mode']}>
						<option value="bcc">{$i18n.t('One email, recipients hidden (Bcc)')}</option>
						<option value="to">{$i18n.t('One email, recipients visible (To)')}</option>
						<option value="separate">{$i18n.t('One email per recipient')}</option>
					</select>
				</AdminSettingRow>
			</AdminSettingSection>

			<AdminSettingSection title="Telegram">
				<!-- Reports are not sent by Telegram for now (backend: REPORTS_PAUSED); email still works. -->
				<p class="text-xs text-gray-500">
					<span class="font-medium">{$i18n.t('Sending reports by Telegram is not supported for now')}.</span>
					{$i18n.t('Schedules that chose Telegram keep that choice but are only sent by email.')}
				</p>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Limits')}>
				<div class="grid grid-cols-2 gap-3">
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Schedules per user')}
						<input type="number" min="1" max="100" class={inputClass} bind:value={values['reports.max_schedules']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Channels per report')}
						<input type="number" min="1" max="500" class={inputClass} bind:value={values['reports.max_channels']} />
					</label>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Default report')}>
				<label for="report-default-prompt" class="text-xs text-gray-500">{$i18n.t('Used when the person who set the schedule leaves their request empty.')}</label>
				<textarea id="report-default-prompt" rows="4" class="{inputClass} resize-y" bind:value={values['reports.default_prompt']}></textarea>
				<div class="grid grid-cols-2 gap-3">
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Model writing the reports (empty = tracker model)')}
						<input class={inputClass} bind:value={values['reports.model']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Comment length limit (characters)')}
						<input type="number" min="300" max="10000" class={inputClass} bind:value={values['reports.max_chars']} />
					</label>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Writing ahead')}>
				<p class="text-xs text-gray-500">
					{$i18n.t('Reports are queued before their send time; the AI writes the comments only while the model is not busy, so chats are not slowed down. At the send time the figures are counted again and sent with the comments written ahead.')}
				</p>
				<div class="grid grid-cols-2 gap-3">
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Queue this many minutes before the send time')}
						<input type="number" min="5" max="720" class={inputClass} bind:value={values['reports.prepare_minutes']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Reports written at the same time')}
						<input type="number" min="1" max="4" class={inputClass} bind:value={values['reports.workers']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Model busy when more requests than this wait in its queue')}
						<input type="number" min="0" max="100" class={inputClass} bind:value={values['reports.busy_waiting']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Or when the first token takes longer than (seconds)')}
						<input type="number" min="3" max="120" class={inputClass} bind:value={values['reports.busy_seconds']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Not written this many minutes after the send time: send without comments')}
						<input type="number" min="0" max="240" class={inputClass} bind:value={values['reports.late_minutes']} />
					</label>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('All report schedules ({{count}})', { count: schedules.length })}>
				<div class="overflow-x-auto rounded-xl border border-gray-100 dark:border-gray-850">
					<table class="w-full min-w-[720px] text-xs">
						<thead class="bg-gray-50 text-left text-gray-500 dark:bg-gray-850">
							<tr>
								<th class="px-2.5 py-2 font-medium">{$i18n.t('Report')}</th>
								<th class="px-2.5 py-2 font-medium">{$i18n.t('Created by')}</th>
								<th class="px-2.5 py-2 font-medium">{$i18n.t('Schedule')}</th>
								<th class="px-2.5 py-2 font-medium">{$i18n.t('Last send')}</th>
								<th class="px-2.5 py-2 font-medium"></th>
							</tr>
						</thead>
						<tbody>
							{#each schedules as schedule (schedule.id)}
								<tr class="border-t border-gray-100 dark:border-gray-850">
									<td class="px-2.5 py-2 font-medium">{schedule.name}{#if !schedule.enabled}<span class="ml-1 text-amber-600">({$i18n.t('Paused')})</span>{/if}</td>
									<td class="px-2.5 py-2">{schedule.owner?.name}</td>
									<td class="px-2.5 py-2">{schedule.when}</td>
									<td class="px-2.5 py-2 {schedule.last_status === 'error' ? 'text-red-600' : ''}" title={schedule.last_error ?? ''}>
										{when(schedule.last_run_at)}{schedule.last_status ? ` · ${schedule.last_status}` : ''}
									</td>
									<td class="whitespace-nowrap px-2.5 py-2 text-right">
										<button type="button" class="px-1 text-blue-600" disabled={running === schedule.id} on:click={() => runNow(schedule)}>{running === schedule.id ? $i18n.t('Sending…') : $i18n.t('Send now')}</button>
										<button type="button" class="px-1 text-blue-600" on:click={() => toggle(schedule)}>{schedule.enabled ? $i18n.t('Pause') : $i18n.t('Resume')}</button>
										<button type="button" class="px-1 text-red-600" on:click={() => remove(schedule)}>{$i18n.t('Delete')}</button>
									</td>
								</tr>
							{:else}
								<tr><td colspan="5" class="px-2.5 py-3 text-gray-500">{$i18n.t('No report schedule yet.')}</td></tr>
							{/each}
						</tbody>
					</table>
				</div>
			</AdminSettingSection>
		{/if}
	</div>
	<div class="flex justify-end pt-6 text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
