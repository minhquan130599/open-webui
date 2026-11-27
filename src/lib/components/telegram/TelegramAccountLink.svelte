<script lang="ts">
	import { createEventDispatcher, getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getTelegramAccountLinkStatus,
		getTelegramMe,
		newTelegramAccountLink,
		unlinkTelegramAccount,
		verifyTelegramAccountLink,
		type TelegramLinkStatus,
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
	// Start was pressed in Telegram: the bot emailed a 6-digit code, entered here to finish the link.
	let otp: TelegramLinkStatus | null = null;
	let otpValue = '';
	let otpError = '';
	let verifying = false;
	// Why the last link stopped (shown until the next try), e.g. email not configured.
	let stopped = '';
	let timer: ReturnType<typeof setInterval> | null = null;
	let busy = false;

	const load = async () => {
		me = await getTelegramMe(localStorage.token).catch(() => null);
	};

	const stopWaiting = () => {
		if (timer) clearInterval(timer);
		timer = null;
		linking = null;
		otp = null;
		otpValue = '';
		otpError = '';
	};

	const stopReason = (error?: string) =>
		error === 'mail'
			? $i18n.t('Linking Telegram needs a confirmation code sent by email, but the administrator has not set up sending email. Contact the administrator.')
			: error === 'send'
				? $i18n.t('The confirmation email could not be sent. Try again in a few minutes or contact the administrator.')
				: error === 'locked'
					? $i18n.t('Too many wrong codes. Press Link Telegram to start again.')
					: error === 'expired'
						? $i18n.t('The confirmation code expired. Press Link Telegram to start again.')
						: error === 'account'
							? $i18n.t('This NCS AI account cannot be used right now (locked or not approved).')
							: $i18n.t('The link code expired. Press Link Telegram again.');

	const finish = async () => {
		stopWaiting();
		await load();
		toast.success($i18n.t('Telegram linked'));
		dispatch('linked', me?.account);
	};

	const poll = async () => {
		if (!linking) return;
		const status = await getTelegramAccountLinkStatus(localStorage.token, linking.code).catch(() => null);
		if (!linking || !status) return;
		if (status.linked) {
			await finish();
			return;
		}
		if (status.gone) {
			stopped = stopReason(status.error);
			stopWaiting();
			toast.error(stopped);
			return;
		}
		if (status.otp) {
			otp = status;
			// The email code has its own 10 minutes, counted from when it was sent.
			linking.until = Date.now() + (status.otp_seconds ?? 0) * 1000;
			return;
		}
		if (Date.now() > linking.until) {
			stopped = stopReason();
			stopWaiting();
			toast.error(stopped);
		}
	};

	export const start = async () => {
		busy = true;
		stopped = '';
		try {
			const result = await newTelegramAccountLink(localStorage.token);
			stopWaiting();
			linking = { code: result.code, url: result.url, until: Date.now() + result.minutes * 60_000 };
			timer = setInterval(poll, 2500);
		} catch (e: any) {
			stopped = e?.detail ?? String(e);
			toast.error(stopped);
		} finally {
			busy = false;
		}
	};

	const verify = async () => {
		if (!linking || verifying) return;
		const value = otpValue.trim();
		if (value.length !== 6) {
			otpError = $i18n.t('Enter the 6-digit code from the email.');
			return;
		}
		verifying = true;
		otpError = '';
		try {
			const result = await verifyTelegramAccountLink(localStorage.token, linking.code, value);
			if (result.status === 'linked') {
				await finish();
			} else if (result.status === 'wrong') {
				otpError = $i18n.t('Wrong code. {{count}} attempts left.', { count: result.attempts_left ?? 0 });
				otpValue = '';
			} else {
				stopped = stopReason(result.status === 'invalid' ? undefined : result.status);
				stopWaiting();
				toast.error(stopped);
			}
		} catch (e: any) {
			otpError = e?.detail ?? String(e);
		} finally {
			verifying = false;
		}
	};

	const unlink = async () => {
		if (!confirm($i18n.t('Unlink Telegram? You will no longer send messages into channels from Telegram.'))) return;
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
				{#if me.can_ask}
					{$i18n.t('Link your Telegram to send messages into NCS AI channels from Telegram. Messages only go one way: the AI does not answer them and nothing is sent back to Telegram.')}
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
		{:else if linking && otp}
			<form class="flex flex-col gap-2 rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-850" on:submit|preventDefault={verify}>
				<span>
					{$i18n.t('A 6-digit confirmation code was sent to {{email}}. Enter it here to link the Telegram account {{telegram}}. The code works for 10 minutes.', {
						email: otp.email || $i18n.t('the email of your account'),
						telegram: otp.telegram?.username ? `@${otp.telegram.username}` : otp.telegram?.display_name || ''
					})}
				</span>
				<span class="text-gray-500">{$i18n.t('Not you pressing Start in Telegram? Press Cancel and do not enter the code.')}</span>
				<div class="flex flex-wrap items-center gap-2">
					<input
						class="w-32 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-center font-mono text-sm tracking-[0.3em] outline-none dark:border-gray-700 dark:bg-gray-900"
						inputmode="numeric"
						autocomplete="one-time-code"
						maxlength="6"
						placeholder="••••••"
						aria-label={$i18n.t('Confirmation code')}
						bind:value={otpValue}
						on:input={() =>
							(otpValue = [...otpValue]
								.filter((c) => c >= '0' && c <= '9')
								.join('')
								.slice(0, 6))}
					/>
					<button
						type="submit"
						class="flex items-center gap-1.5 rounded-lg bg-gray-900 px-3 py-1.5 font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
						disabled={verifying || otpValue.length !== 6}
					>
						{#if verifying}<Spinner className="size-3" />{/if}
						{$i18n.t('Confirm')}
					</button>
				</div>
				{#if otpError}<span class="text-red-600">{otpError}</span>{/if}
				<button type="button" class="w-fit text-gray-500 underline" on:click={stopWaiting}>{$i18n.t('Cancel')}</button>
			</form>
		{:else if linking}
			<div class="flex flex-col gap-2 rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-850">
				<span>{$i18n.t('Press Open Telegram, then Start in the chat with the bot. A confirmation code is then sent to your email, to be entered here. The link works for 10 minutes.')}</span>
				<a class="w-fit rounded-lg bg-[#229ED9] px-3 py-1.5 font-medium text-white" href={linking.url} target="_blank" rel="noopener">{$i18n.t('Open Telegram')}</a>
				<span class="flex items-center gap-2 text-gray-500"><Spinner className="size-3" />{$i18n.t('Waiting for Start in Telegram…')}</span>
				<button type="button" class="w-fit text-gray-500 underline" on:click={stopWaiting}>{$i18n.t('Cancel')}</button>
			</div>
		{:else}
			{#if stopped}<p class="text-xs text-red-600">{stopped}</p>{/if}
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
