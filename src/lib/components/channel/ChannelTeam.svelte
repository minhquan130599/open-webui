<script lang="ts">
	// "Members & roles" of a project channel: the owner and managers (team leads), who is left out of work given to
	// everyone, the channel's roles / teams ("AM", "SOC") the tracker assigns "các AM" to, and name aliases.
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deleteChannelAlias,
		setChannelLeftOut,
		setChannelManager,
		setChannelRoles,
		transferChannelOwner,
		type ChannelTeam
	} from '$lib/apis/tracker';
	import ConfirmDialog from '../common/ConfirmDialog.svelte';

	const i18n: any = getContext('i18n');

	export let channelId: string;
	export let team: ChannelTeam;
	export let onChanged = () => {};

	let busy = false;
	let editingRoles = false;
	let draft: { ten: string; ids: string[] }[] = [];
	let transferTo: { id: string; name: string } | null = null;
	let showTransfer = false;

	$: active = team.members.filter((m) => !m.left);
	$: nameOf = Object.fromEntries(team.members.map((m) => [m.id, m.name]));

	const run = async (action: () => Promise<unknown>, done: string) => {
		busy = true;
		try {
			await action();
			toast.success(done);
			onChanged();
		} catch (error: any) {
			toast.error(error?.detail ?? `${error}`);
		} finally {
			busy = false;
		}
	};

	const toggleManager = (id: string, manager: boolean) =>
		run(
			() => setChannelManager(localStorage.token, channelId, id, manager),
			manager ? $i18n.t('Manager added') : $i18n.t('Manager removed')
		);

	const toggleLeftOut = (id: string, on: boolean) => {
		const ids = team.members.filter((m) => m.left_out && m.id !== id).map((m) => m.id);
		return run(
			() => setChannelLeftOut(localStorage.token, channelId, on ? [...ids, id] : ids),
			$i18n.t('Saved')
		);
	};

	const editRoles = () => {
		draft = team.roles.map((r) => ({ ten: r.ten, ids: [...r.ids] }));
		if (!draft.length) draft = [{ ten: '', ids: [] }];
		editingRoles = true;
	};

	const saveRoles = () =>
		run(async () => {
			await setChannelRoles(
				localStorage.token,
				channelId,
				draft.filter((r) => r.ten.trim()).map((r) => ({ ten: r.ten.trim(), ids: r.ids }))
			);
			editingRoles = false;
		}, $i18n.t('Roles saved'));

	const toggleInRole = (index: number, id: string) => {
		const role = draft[index];
		role.ids = role.ids.includes(id) ? role.ids.filter((i) => i !== id) : [...role.ids, id];
		draft = draft;
	};
</script>

<div class="flex flex-col gap-3 px-1 pb-3 text-xs">
	<section class="flex flex-col gap-1.5">
		<div class="font-medium text-gray-700 dark:text-gray-300">{$i18n.t('Members')}</div>
		<p class="text-[0.6875rem] leading-snug text-gray-500">
			{$i18n.t(
				'Managers (team leads) run the progress like the owner. People left out are not given work meant for everyone ("mọi người", "toàn team"); admins, the owner and managers always are.'
			)}
		</p>
		{#each team.members as member (member.id)}
			<div
				class="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-gray-100 px-2 py-1.5 dark:border-gray-800 {member.left
					? 'opacity-60'
					: ''}"
			>
				<span class="min-w-0 flex-1 truncate text-gray-800 dark:text-gray-200" title={member.email}
					>{member.name}</span
				>
				{#if member.is_owner}
					<span class="rounded-full bg-blue-50 px-1.5 py-0.5 text-[0.625rem] text-blue-700 dark:bg-blue-950 dark:text-blue-300"
						>{$i18n.t('Owner')}</span
					>
				{/if}
				{#if member.is_manager}
					<span class="rounded-full bg-violet-50 px-1.5 py-0.5 text-[0.625rem] text-violet-700 dark:bg-violet-950 dark:text-violet-300"
						>{$i18n.t('Manager')}</span
					>
				{/if}
				{#if member.is_admin}
					<span class="rounded-full bg-gray-100 px-1.5 py-0.5 text-[0.625rem] text-gray-600 dark:bg-gray-800 dark:text-gray-300"
						>Admin</span
					>
				{/if}
				{#if member.left}
					<span class="rounded-full bg-red-50 px-1.5 py-0.5 text-[0.625rem] text-red-700 dark:bg-red-950 dark:text-red-300"
						>{$i18n.t('Left')}</span
					>
				{/if}
				{#if team.can_manage && !member.left}
					<label class="flex items-center gap-1 text-[0.6875rem] text-gray-500" title={member.left_out_auto ? $i18n.t('Always left out') : ''}>
						<input
							type="checkbox"
							disabled={busy || member.left_out_auto}
							checked={member.left_out || member.left_out_auto}
							on:change={(e) => toggleLeftOut(member.id, e.currentTarget.checked)}
						/>
						{$i18n.t('Not given shared work')}
					</label>
				{/if}
				{#if team.can_change_owner && !member.is_owner && !member.left}
					<button
						type="button"
						class="rounded-md px-1.5 py-0.5 text-[0.6875rem] text-violet-700 hover:bg-violet-50 dark:text-violet-300 dark:hover:bg-violet-950/40"
						disabled={busy}
						on:click={() => toggleManager(member.id, !member.is_manager)}
						>{member.is_manager ? $i18n.t('Remove manager') : $i18n.t('Make manager')}</button
					>
					<button
						type="button"
						class="rounded-md px-1.5 py-0.5 text-[0.6875rem] text-blue-700 hover:bg-blue-50 dark:text-blue-300 dark:hover:bg-blue-950/40"
						disabled={busy}
						on:click={() => {
							transferTo = { id: member.id, name: member.name };
							showTransfer = true;
						}}>{$i18n.t('Make owner')}</button
					>
				{/if}
			</div>
		{/each}
	</section>

	<section class="flex flex-col gap-1.5">
		<div class="flex items-center justify-between">
			<span class="font-medium text-gray-700 dark:text-gray-300">{$i18n.t('Roles and teams')}</span>
			{#if team.can_manage && !editingRoles}
				<button type="button" class="text-[0.6875rem] text-blue-600 hover:underline" on:click={editRoles}
					>{$i18n.t('Edit')}</button
				>
			{/if}
		</div>
		<p class="text-[0.6875rem] leading-snug text-gray-500">
			{$i18n.t(
				'Work given to a role ("các AM", "team SOC") goes to its people. Learned from messages like "AM: @A, @B"; a role edited here is kept as is.'
			)}
		</p>
		{#if editingRoles}
			{#each draft as role, index}
				<div class="flex flex-col gap-1 rounded-lg border border-gray-100 p-2 dark:border-gray-800">
					<div class="flex items-center gap-2">
						<input
							class="min-w-0 flex-1 rounded-md border border-gray-200 bg-transparent px-2 py-1 dark:border-gray-700"
							placeholder={$i18n.t('Role or team, e.g. AM, SOC')}
							bind:value={role.ten}
						/>
						<button
							type="button"
							class="text-[0.6875rem] text-red-600 hover:underline"
							on:click={() => (draft = draft.filter((_, i) => i !== index))}>{$i18n.t('Remove')}</button
						>
					</div>
					<div class="flex flex-wrap gap-1">
						{#each active as member (member.id)}
							<button
								type="button"
								class="rounded-full border px-2 py-0.5 text-[0.6875rem] {role.ids.includes(member.id)
									? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
									: 'border-gray-200 text-gray-600 dark:border-gray-700 dark:text-gray-300'}"
								on:click={() => toggleInRole(index, member.id)}>{member.name}</button
							>
						{/each}
					</div>
				</div>
			{/each}
			<div class="flex gap-2">
				<button
					type="button"
					class="rounded-md border border-gray-200 px-2 py-1 dark:border-gray-700"
					on:click={() => (draft = [...draft, { ten: '', ids: [] }])}>+ {$i18n.t('Add role')}</button
				>
				<span class="flex-1"></span>
				<button type="button" class="px-2 py-1 text-gray-500" on:click={() => (editingRoles = false)}
					>{$i18n.t('Cancel')}</button
				>
				<button
					type="button"
					class="rounded-md bg-gray-900 px-2.5 py-1 text-white dark:bg-white dark:text-gray-900"
					disabled={busy}
					on:click={saveRoles}>{$i18n.t('Save')}</button
				>
			</div>
		{:else if team.roles.length}
			{#each team.roles as role (role.key)}
				<div class="flex flex-wrap items-center gap-1">
					<span class="font-medium text-gray-800 dark:text-gray-200">{role.ten}:</span>
					<span class="text-gray-600 dark:text-gray-300"
						>{role.ids.map((id) => nameOf[id]).filter(Boolean).join(', ') || role.nguoi.join(', ') || '—'}</span
					>
					{#if !role.sua_tay}
						<span class="text-[0.625rem] text-gray-400">({$i18n.t('learned from messages')})</span>
					{/if}
				</div>
			{/each}
		{:else}
			<span class="text-gray-500">{$i18n.t('No roles yet')}</span>
		{/if}
	</section>

	{#if team.aliases.length}
		<section class="flex flex-col gap-1">
			<span class="font-medium text-gray-700 dark:text-gray-300">{$i18n.t('Name aliases')}</span>
			{#each team.aliases as alias (alias.ten)}
				<div class="flex items-center gap-2 text-gray-600 dark:text-gray-300">
					<span>"{alias.ten}" → {alias.user || alias.user_id}</span>
					{#if team.can_manage}
						<button
							type="button"
							class="text-[0.6875rem] text-red-600 hover:underline"
							disabled={busy}
							on:click={() =>
								run(() => deleteChannelAlias(localStorage.token, channelId, alias.ten), $i18n.t('Removed'))}
							>{$i18n.t('Remove')}</button
						>
					{/if}
				</div>
			{/each}
		</section>
	{/if}
</div>

<ConfirmDialog
	bind:show={showTransfer}
	title={$i18n.t('Change channel owner')}
	message={$i18n.t(
		'Make {{name}} the owner of this channel? You stay as a manager. Messages, files and work items are kept.',
		{ name: transferTo?.name ?? '' }
	)}
	onConfirm={() => {
		const target = transferTo;
		transferTo = null;
		if (target)
			run(
				() => transferChannelOwner(localStorage.token, channelId, target.id),
				$i18n.t('Channel owner changed')
			);
	}}
	on:cancel={() => (transferTo = null)}
/>
