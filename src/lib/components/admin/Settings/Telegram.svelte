<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		adminUnlinkTelegram,
		getTelegramAccounts,
		getTelegramAdminSettings,
		getTelegramStatus,
		saveTelegramAdminSettings,
		testTelegramBot,
		type TelegramAdminAccount
	} from '$lib/apis/telegram';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { models } from '$lib/stores';
	import Switch from '$lib/components/common/Switch.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	export let saveHandler: () => void = () => {};

	let values: Record<string, any> | null = null;
	let botToken = '';
	let check = '';
	let status: Record<string, any> = {};
	let accounts: TelegramAdminAccount[] = [];
	let channels: { id: string; name: string; type?: string }[] = [];
	let channelFilter = '';

	const inputClass =
		'w-full rounded-lg border border-gray-100 bg-transparent px-2.5 py-1.5 text-sm outline-none dark:border-gray-850';

	const loadChannels = async () => {
		const res = await fetch(`${WEBUI_API_BASE_URL}/channels/list`, {
			headers: { authorization: `Bearer ${localStorage.token}` }
		}).catch(() => null);
		channels = res?.ok ? (await res.json()).filter((c: any) => c.type !== 'dm') : [];
	};

	onMount(async () => {
		try {
			values = await getTelegramAdminSettings(localStorage.token);
			[status, accounts] = await Promise.all([
				getTelegramStatus(localStorage.token).catch(() => ({})),
				getTelegramAccounts(localStorage.token).catch(() => [])
			]);
			await loadChannels();
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		}
	});

	const save = async () => {
		if (!values) return;
		const changes: Record<string, any> = {};
		for (const [key, value] of Object.entries(values)) {
			if (key !== 'telegram.bot_token_set' && key !== 'telegram.bot_username') changes[key] = value;
		}
		if (botToken) changes['telegram.bot_token'] = botToken;
		try {
			values = await saveTelegramAdminSettings(localStorage.token, changes);
			botToken = '';
			saveHandler();
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		}
	};

	const testBot = async () => {
		check = $i18n.t('Checking…');
		const result: any = await testTelegramBot(localStorage.token, botToken).catch((e) => ({
			ok: false,
			error: e?.detail ?? String(e)
		}));
		check = result.ok
			? $i18n.t('Connected to bot @{{name}}', { name: result.username })
			: `${$i18n.t('Error')}: ${result.error}`;
		if (result.ok && !botToken && values) values = { ...values, 'telegram.bot_username': result.username };
	};

	const unlink = async (account: TelegramAdminAccount) => {
		if (!confirm($i18n.t('Unlink the Telegram of {{name}}?', { name: account.user_name || account.user_email }))) return;
		await adminUnlinkTelegram(localStorage.token, account.user_id).catch((e) => toast.error(e?.detail ?? String(e)));
		accounts = accounts.filter((a) => a.user_id !== account.user_id);
	};

	const toggleBlocked = (id: string) => {
		if (!values) return;
		const list: string[] = values['telegram.blocked_channels'] ?? [];
		values['telegram.blocked_channels'] = list.includes(id) ? list.filter((c) => c !== id) : [...list, id];
	};

	const ago = (seconds?: number) =>
		seconds ? new Date(seconds * 1000).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
	const when = (ns: number | null) => (ns ? ago(Math.floor(ns / 1e9)) : '—');

	$: conflict = status?.conflict_at && Date.now() / 1000 - status.conflict_at < 3600;
	$: shownChannels = channels.filter((c) => !channelFilter || c.name.toLowerCase().includes(channelFilter.toLowerCase()));
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={save}>
	<h2 class="mb-4 text-sm font-medium text-gray-900 dark:text-white">{$i18n.t('settings.admin.telegram.title')}</h2>

	<div class="scrollbar-hover min-h-0 flex-1 overflow-y-auto pr-1.5">
		{#if values}
			<AdminSettingSection title={$i18n.t('Bot')} first>
				<p class="text-xs text-gray-500">
					{$i18n.t('One bot for scheduled reports and for asking the AI. Use a separate bot for each environment (server, test, dev): two apps reading the same bot take messages from each other.')}
				</p>
				<div class="flex flex-col gap-1.5">
					<label for="telegram-bot-token" class="text-xs text-gray-500">
						{$i18n.t('Bot token (from @BotFather)')}
						{#if values['telegram.bot_token_set']}· {$i18n.t('saved; leave empty to keep it')}{/if}
					</label>
					<div class="flex gap-2">
						<input id="telegram-bot-token" type="password" class={inputClass} autocomplete="off" bind:value={botToken} placeholder={values['telegram.bot_token_set'] ? '••••••••' : ''} />
						<button type="button" class="shrink-0 rounded-lg border border-gray-100 px-2.5 text-xs dark:border-gray-850" on:click={testBot}>{$i18n.t('Check')}</button>
					</div>
					{#if check}<span class="text-xs text-gray-500">{check}</span>{/if}
					{#if values['telegram.bot_username']}<span class="text-xs text-gray-500">Bot: @{values['telegram.bot_username']}</span>{/if}
				</div>
				<div class="rounded-lg bg-gray-50 p-2.5 text-xs dark:bg-gray-850">
					<div>
						{$i18n.t('Reading messages')}:
						<span class={status?.reading ? 'text-green-600' : 'text-gray-500'}>{status?.reading ? $i18n.t('yes') : $i18n.t('no')}</span>
						· {$i18n.t('last OK')}: {ago(status?.last_ok)}
					</div>
					{#if status?.error}
						<div class="text-red-600">{$i18n.t('Last error')} ({ago(status?.error_at)}): {status.error}</div>
					{/if}
					{#if conflict}
						<div class="mt-1 font-medium text-amber-600">
							{$i18n.t('Another app is reading this bot (409 Conflict). Give each environment its own bot, otherwise links and questions go to the wrong app.')}
						</div>
					{/if}
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Features')}>
				<AdminSettingRow label={$i18n.t('Send scheduled reports by Telegram')} let:labelId>
					<Switch ariaLabelledbyId={labelId} bind:state={values['telegram.reports']} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('Ask the AI from Telegram')}
					description={$i18n.t('Users with the "Telegram" permission (Users → Groups → Permissions; admins always) link their Telegram and ask as themselves, with the same access as on the web.')}
					let:labelId
				>
					<Switch ariaLabelledbyId={labelId} bind:state={values['telegram.ask']} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('Ask inside channels')}
					description={$i18n.t('The question is posted in the channel under the name of the user, like mentioning the AI on the web.')}
					let:labelId
				>
					<Switch ariaLabelledbyId={labelId} bind:state={values['telegram.ask_channels']} />
				</AdminSettingRow>
				<AdminSettingRow label={$i18n.t('Send files to Telegram (PDF, Word, Excel, attachments)')} let:labelId>
					<Switch ariaLabelledbyId={labelId} bind:state={values['telegram.files']} />
				</AdminSettingRow>
				<p class="text-xs text-gray-500">{$i18n.t('Linking Telegram groups is switched off for now.')}</p>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Limits')}>
				<div class="grid grid-cols-2 gap-3">
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Messages per user per minute')}
						<input type="number" min="1" max="120" class={inputClass} bind:value={values['telegram.rate_per_minute']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Questions answered at the same time (whole app)')}
						<input type="number" min="1" max="50" class={inputClass} bind:value={values['telegram.max_running']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Start a new conversation after (hours idle)')}
						<input type="number" min="1" max="168" class={inputClass} bind:value={values['telegram.new_chat_hours']} />
					</label>
					<label class="flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Longest wait for an answer (minutes)')}
						<input type="number" min="1" max="30" class={inputClass} bind:value={values['telegram.answer_minutes']} />
					</label>
					<label class="col-span-2 flex flex-col gap-1 text-xs text-gray-500">
						{$i18n.t('Default model for Telegram (empty = default model of the user)')}
						<select class={inputClass} bind:value={values['telegram.model']}>
							<option value="">—</option>
							{#each $models as model}
								<option value={model.id}>{model.name}</option>
							{/each}
						</select>
					</label>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Channels that cannot be asked from Telegram ({{count}})', { count: (values['telegram.blocked_channels'] ?? []).length })}>
				<input class={inputClass} placeholder={$i18n.t('Search channels')} bind:value={channelFilter} />
				<div class="max-h-48 overflow-y-auto rounded-lg border border-gray-100 p-2 dark:border-gray-850">
					{#each shownChannels as channel (channel.id)}
						<label class="flex items-center gap-2 py-0.5 text-xs">
							<input type="checkbox" checked={(values['telegram.blocked_channels'] ?? []).includes(channel.id)} on:change={() => toggleBlocked(channel.id)} />
							#{channel.name}
						</label>
					{:else}
						<div class="text-xs text-gray-500">{$i18n.t('No channel')}</div>
					{/each}
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Linked Telegram accounts ({{count}})', { count: accounts.length })}>
				<div class="overflow-x-auto rounded-xl border border-gray-100 dark:border-gray-850">
					<table class="w-full min-w-[560px] text-xs">
						<thead class="bg-gray-50 text-left text-gray-500 dark:bg-gray-850">
							<tr>
								<th class="px-2.5 py-2 font-medium">{$i18n.t('User')}</th>
								<th class="px-2.5 py-2 font-medium">Telegram</th>
								<th class="px-2.5 py-2 font-medium">{$i18n.t('Linked')}</th>
								<th class="px-2.5 py-2 font-medium">{$i18n.t('Last used')}</th>
								<th class="px-2.5 py-2 font-medium"></th>
							</tr>
						</thead>
						<tbody>
							{#each accounts as account (account.user_id)}
								<tr class="border-t border-gray-100 dark:border-gray-850">
									<td class="px-2.5 py-2">{account.user_name}<span class="block text-gray-500">{account.user_email}</span></td>
									<td class="px-2.5 py-2">{account.username ? `@${account.username}` : account.display_name}</td>
									<td class="px-2.5 py-2">{when(account.linked_at)}</td>
									<td class="px-2.5 py-2">{when(account.last_seen_at)}</td>
									<td class="px-2.5 py-2 text-right">
										<button type="button" class="px-1 text-red-600" on:click={() => unlink(account)}>{$i18n.t('Unlink')}</button>
									</td>
								</tr>
							{:else}
								<tr><td colspan="5" class="px-2.5 py-3 text-gray-500">{$i18n.t('Nobody has linked Telegram yet.')}</td></tr>
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
