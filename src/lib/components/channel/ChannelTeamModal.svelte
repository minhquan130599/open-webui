<script lang="ts">
	// "Members & roles" of a channel, opened from Edit Channel: managers (team leads), owner handover, people left out
	// of work given to everyone, roles / teams, aliases, with a guide to how the progress tracker uses them.
	import { getContext } from 'svelte';

	import { getChannelTeam, type ChannelTeam as ChannelTeamData } from '$lib/apis/tracker';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import ChannelTeam from './ChannelTeam.svelte';

	const i18n: any = getContext('i18n');

	export let show = false;
	export let channel: any = null;

	let team: ChannelTeamData | null = null;
	let error = '';
	let showGuide = false;

	const load = async () => {
		if (!channel?.id) return;
		error = '';
		try {
			team = await getChannelTeam(localStorage.token, channel.id);
		} catch (e: any) {
			error = e?.status === 404 ? $i18n.t('Project progress tracking is not enabled') : (e?.detail ?? `${e}`);
		}
	};

	$: if (show) load();

	const GUIDE = [
		[
			'Managers (team leads)',
			'Run the channel progress like the owner: assign, close and cancel work items, edit roles, see the team view, receive the daily summary and the Monday "to assign" reminder. The owner and admins name or remove managers.'
		],
		[
			'Changing the owner',
			'The owner or an admin hands the channel to another member; the previous owner stays as a manager. Messages, files and work items are kept, so a lead leaving the company does not mean a new channel.'
		],
		[
			'When the owner account is disabled',
			'Every morning the system picks a new owner, in this order: (1) the earliest named manager; (2) a system admin of the channel (group channel: a member; standard channel: someone who wrote in it); (3) whoever wrote the most in the channel over the last 90 days; (4) group channel: the earliest member, standard channel: the longest-standing system admin. Only active accounts count. The new owner is notified. Name a manager before a lead leaves so the choice is yours.'
		],
		[
			'Work given to everyone',
			'"mọi người", "toàn team", "các nhân sự" (no @): every member except system admins, the owner, managers, the sender and people marked "Not given shared work". "@all", "@mọi người": every member except the sender, admins and the owner included.'
		],
		[
			'Roles and teams',
			'"các AM", "team SOC": the people of that role. While the channel has not declared it, the item is owned by the team itself ("AM") and moves to its people once declared. Learned from messages such as "AM: @A, @B", or edited here (an edited role is kept as is).'
		],
		[
			'Same names',
			'Tag people with @ to be certain: a tag carries the account. A name two members share, written without a tag, is not assigned; the owner chooses.'
		],
		[
			'Aliases',
			'When you assign a name that matches no account ("Duy") by hand, you can remember it for this channel. Not offered for a name two members share.'
		]
	];
</script>

{#if channel}
	<Modal ariaLabel={$i18n.t('Members & roles')} size="md" bind:show>
		<div>
			<div class="flex justify-between dark:text-gray-100 px-4 pt-3 mb-1">
				<div class="self-center text-base">{$i18n.t('Members & roles')}</div>
				<button
					aria-label={$i18n.t('Close')}
					class="self-center rounded-lg py-1 text-gray-500 transition hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 px-0"
					on:click={() => (show = false)}
				>
					<XMark className="size-4" />
				</button>
			</div>

			<div class="flex flex-col gap-3 px-4 pb-4 text-sm dark:text-gray-200">
				<div class="rounded-xl border border-gray-100 dark:border-gray-800">
					<button
						type="button"
						class="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-gray-700 dark:text-gray-300"
						aria-expanded={showGuide}
						on:click={() => (showGuide = !showGuide)}
					>
						{$i18n.t('Guide')}
						<span class="text-gray-400">{showGuide ? '−' : '+'}</span>
					</button>
					{#if showGuide}
						<dl class="flex flex-col gap-2 px-3 pb-3 text-xs leading-snug">
							{#each GUIDE as [title, text]}
								<div>
									<dt class="font-medium text-gray-800 dark:text-gray-200">{$i18n.t(title)}</dt>
									<dd class="text-gray-600 dark:text-gray-400">{$i18n.t(text)}</dd>
								</div>
							{/each}
						</dl>
					{/if}
				</div>

				{#if error}
					<div class="text-xs text-red-600">{error}</div>
				{:else if !team}
					<div class="flex justify-center py-6"><Spinner className="size-5" /></div>
				{:else}
					<ChannelTeam channelId={channel.id} {team} onChanged={load} />
				{/if}
			</div>
		</div>
	</Modal>
{/if}
