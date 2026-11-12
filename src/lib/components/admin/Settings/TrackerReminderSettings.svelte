<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getReminderSettings,
		sendRemindersNow,
		updateReminderSettings,
		type ReminderSettings
	} from '$lib/apis/tracker';
	import Switch from '$lib/components/common/Switch.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';

	const i18n: any = getContext('i18n');

	let settings: ReminderSettings | null = null;
	let saved = '';
	let sending = false;

	onMount(async () => {
		try {
			settings = await getReminderSettings(localStorage.token);
			saved = JSON.stringify(settings);
		} catch {
			settings = null; // tracker disabled on this server
		}
	});

	// Called by the General settings form when the admin saves.
	export const save = async () => {
		if (!settings || JSON.stringify(settings) === saved) return;
		if (!/^([01]?\d|2[0-3]):[0-5]\d$/.test(settings.time)) {
			throw new Error($i18n.t('Reminder time must be HH:MM'));
		}
		settings = await updateReminderSettings(localStorage.token, settings);
		saved = JSON.stringify(settings);
	};

	const sendNow = async () => {
		sending = true;
		try {
			const result = await sendRemindersNow(localStorage.token);
			toast.success($i18n.t('{{count}} reminders sent', { count: result.created }));
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		} finally {
			sending = false;
		}
	};
</script>

{#if settings}
	<AdminSettingRow
		label={$i18n.t('Due date reminders')}
		description={$i18n.t(
			'Send a notification to the owner of each work item that is due, once a day at the set time.'
		)}
		labelClassName="text-gray-500 dark:text-gray-500"
		let:labelId
	>
		<Switch bind:state={settings.enable} ariaLabelledbyId={labelId} />
	</AdminSettingRow>
	{#if settings.enable}
		<AdminSettingRow
			label={$i18n.t('Reminder time')}
			description={settings.timezone
				? $i18n.t('Time zone: {{timezone}}', { timezone: settings.timezone })
				: ''}
			labelClassName="text-gray-500 dark:text-gray-500"
			let:labelId
		>
			<input
				type="time"
				bind:value={settings.time}
				aria-labelledby={labelId}
				class="rounded-lg bg-transparent border border-gray-200 dark:border-gray-800 px-2 py-1 text-xs outline-none"
			/>
		</AdminSettingRow>
		<AdminSettingRow
			label={$i18n.t('Also remind 1 day before')}
			labelClassName="text-gray-500 dark:text-gray-500"
			let:labelId
		>
			<Switch bind:state={settings.day_before} ariaLabelledbyId={labelId} />
		</AdminSettingRow>
		<AdminSettingRow
			label={$i18n.t('Send reminders now')}
			description={$i18n.t('Work items already reminded today are not sent again.')}
			labelClassName="text-gray-500 dark:text-gray-500"
		>
			<button
				type="button"
				class="rounded-lg px-2.5 py-1 text-xs bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 transition disabled:opacity-50"
				disabled={sending}
				on:click={sendNow}
			>
				{$i18n.t('Send')}
			</button>
		</AdminSettingRow>
	{/if}
{/if}
