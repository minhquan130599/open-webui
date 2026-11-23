<script lang="ts">
	import { createEventDispatcher, getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getTelegramAccountLinkStatus,
		getTelegramMe,
		newTelegramAccountLink,
		unlinkTelegramAccount,
		type TelegramMe
	} from '$lib/apis/telegram';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n: any = getContext('i18n');
	const dispatch = createEventDispatcher();

	/** compact: one line (used inside the report form) */
	export let compact = false;
	/** start linking right away (e.g. the "Link" button of the report form) */
	export let autoStart = false;

	let me: TelegramMe | null = null;
	let linking: { code: string; url: string; until: number } | null = null;
	let timer: ReturnType<typeof setInterval> | null = null;
	let busy = false;

	const load = async () => {
		me = await getTelegramMe(localStorage.token).catch(() => null);
	};

	const stopWaiting = () => {
		if (timer) clearInterval(timer);
		timer = null;
		linking = null;
	};

	export const start = async () => {
		busy = true;
		try {
			const result = await newTelegramAccountLink(localStorage.token);
			linking = { code: result.code, url: result.url, until: Date.now() + result.minutes * 60_000 };
			if (timer) clearInterval(timer);
			timer = setInterval(async () => {
				if (!linking) return;
				if (Date.now() > linking.until) {
					stopWaiting();
					toast.error($i18n.t('The link code expired. Press Link Telegram again.'));
					return;
				}
				const status = await getTelegramAccountLinkStatus(localStorage.token, linking.code).catch(() => null);
				if (status?.linked) {
					stopWaiting();
					await load();
					toast.success($i18n.t('Telegram linked'));
					dispatch('linked', me?.account);
				}
			}, 2500);
		} catch (e: any) {
			toast.error(e?.detail ?? String(e));
		} finally {
			busy = false;
		}
	};

	const unlink = async () => {
		if (!confirm($i18n.t('Unlink Telegram? You will no longer ask the AI or receive reports there.'))) return;
		await unlinkTelegramAccount(localStorage.token).catch((e) => toast.error(e?.detail ?? String(e)));
		await load();
		dispatch('unlinked');
	};

	onMount(async () => {
		await load();
		if (autoStart && me?.available && !me.account) await start();
	});
	onDestroy(stopWaiting);
</script>

{#if me && (me.available || me.account)}
	<div class="flex flex-col gap-2 {compact ? '' : 'rounded-xl border border-gray-100 p-3.5 dark:border-gray-850'}">
		{#if !compact}
			<div class="flex items-center justify-between gap-2">
				<div class="text-sm font-medium">Telegram</div>
				{#if me.account}
					<span class="text-xs text-green-600">{$i18n.t('Linked')}</span>
				{/if}
			</div>
			<p class="text-xs text-gray-500">
				{#if me.can_ask && me.reports}
					{$i18n.t('Link your Telegram to ask the AI (directly or in channels) and receive scheduled reports there.')}
				{:else if me.can_ask}
					{$i18n.t('Link your Telegram to ask the AI directly or in channels from Telegram.')}
				{:else}
					{$i18n.t('Link your Telegram to receive scheduled reports there.')}
				{/if}
			</p>
		{/if}

		{#if me.account}
			<div class="flex flex-wrap items-center justify-between gap-2 text-sm">
				<span>
					{me.account.username ? `@${me.account.username}` : me.account.display_name}
					{#if me.bot}<span class="text-xs text-gray-500"> · {$i18n.t('bot')} @{me.bot}</span>{/if}
				</span>
				<div class="flex gap-2">
					{#if me.bot}
						<a class="rounded-lg border border-gray-200 px-2.5 py-1 text-xs dark:border-gray-700" href={`https://t.me/${me.bot}`} target="_blank" rel="noopener">{$i18n.t('Open Telegram')}</a>
					{/if}
					<button type="button" class="rounded-lg px-2.5 py-1 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30" on:click={unlink}>{$i18n.t('Unlink')}</button>
				</div>
			</div>
		{:else if linking}
			<div class="flex flex-col gap-2 rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-850">
				<span>{$i18n.t('Press Open Telegram, then Start in the chat with the bot. The link works for 10 minutes.')}</span>
				<a class="w-fit rounded-lg bg-[#229ED9] px-3 py-1.5 font-medium text-white" href={linking.url} target="_blank" rel="noopener">{$i18n.t('Open Telegram')}</a>
				<span class="flex items-center gap-2 text-gray-500"><Spinner className="size-3" />{$i18n.t('Waiting for the chat to be linked…')}</span>
				<button type="button" class="w-fit text-gray-500 underline" on:click={stopWaiting}>{$i18n.t('Cancel')}</button>
			</div>
		{:else}
			<button
				type="button"
				class="w-fit rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs transition hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-850"
				disabled={busy}
				on:click={start}
			>
				{$i18n.t('Link Telegram')}
			</button>
		{/if}
	</div>
{/if}
