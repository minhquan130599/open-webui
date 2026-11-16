<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getFilePipelineSettings, updateFilePipelineSettings } from '$lib/apis/retrieval';
	import Switch from '$lib/components/common/Switch.svelte';
	import AdminSettingField from '../AdminSettingField.svelte';
	import AdminSettingRow from '../AdminSettingRow.svelte';
	import AdminSettingSection from '../AdminSettingSection.svelte';

	export let inputClass = '';

	type Setting = {
		key: string;
		label: string;
		type: 'bool' | 'int' | 'text';
		help?: string;
		min?: number;
		max?: number;
		default: unknown;
		value: any;
	};

	let title = '';
	let note = '';
	let settings: Setting[] = [];
	let values: Record<string, any> = {};
	let saved: Record<string, any> = {};
	let saving = false;

	const load = async () => {
		try {
			const res = await getFilePipelineSettings(localStorage.token);
			title = res.title;
			note = res.note;
			settings = res.settings;
			values = Object.fromEntries(settings.map((s) => [s.key, s.value]));
			saved = { ...values };
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	$: changed = Object.keys(values).filter((key) => values[key] !== saved[key]);

	const save = async () => {
		saving = true;
		try {
			const res = await updateFilePipelineSettings(
				localStorage.token,
				Object.fromEntries(changed.map((key) => [key, values[key]]))
			);
			settings = res.settings;
			values = Object.fromEntries(settings.map((s) => [s.key, s.value]));
			saved = { ...values };
			toast.success('Đã lưu cài đặt xử lý file');
		} catch (error) {
			toast.error(`${error}`);
		}
		saving = false;
	};

	onMount(load);
</script>

{#if settings.length}
	<AdminSettingSection {title}>
		{#if note}
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600">{note}</p>
		{/if}
		{#each settings as setting (setting.key)}
			{#if setting.type === 'bool'}
				<AdminSettingRow label={setting.label} description={setting.help ?? ''} let:labelId>
					<Switch bind:state={values[setting.key]} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
			{:else}
				<AdminSettingField
					label={setting.label}
					description={setting.help ?? ''}
					forId={`file-setting-${setting.key}`}
				>
					{#if setting.type === 'int'}
						<input
							id={`file-setting-${setting.key}`}
							class={inputClass}
							type="number"
							min={setting.min}
							max={setting.max}
							placeholder={String(setting.default ?? '')}
							bind:value={values[setting.key]}
							autocomplete="off"
						/>
					{:else}
						<input
							id={`file-setting-${setting.key}`}
							class={inputClass}
							type="text"
							placeholder={String(setting.default ?? '')}
							bind:value={values[setting.key]}
							autocomplete="off"
						/>
					{/if}
				</AdminSettingField>
			{/if}
		{/each}
		<div class="flex justify-end">
			<button
				type="button"
				class="px-3 py-1 text-xs font-medium rounded-lg bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-gray-100 disabled:opacity-50 transition"
				disabled={saving || changed.length === 0}
				on:click={save}
			>
				{saving ? 'Đang lưu…' : changed.length ? `Lưu ${changed.length} thay đổi` : 'Đã lưu'}
			</button>
		</div>
	</AdminSettingSection>
{/if}
