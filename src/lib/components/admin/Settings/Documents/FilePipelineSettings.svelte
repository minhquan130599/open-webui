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
		section?: string;
		advanced?: boolean;
		show_if?: Record<string, unknown[]>;
	};
	type Section = { id: string; title: string; note: string };

	let title = '';
	let note = '';
	let sections: Section[] = [];
	let settings: Setting[] = [];
	let values: Record<string, any> = {};
	let saved: Record<string, any> = {};
	let open: Record<string, boolean> = {};

	const apply = (res: any) => {
		title = res.title;
		note = res.note;
		sections = res.sections ?? [{ id: '', title: '', note: '' }];
		settings = res.settings;
		values = Object.fromEntries(settings.map((s) => [s.key, s.value]));
		saved = { ...values };
	};

	const load = async () => {
		try {
			apply(await getFilePipelineSettings(localStorage.token));
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	// Saved together with the rest of the Documents page (its Save button calls this).
	export const save = async () => {
		if (changed.length === 0) return true;
		try {
			apply(
				await updateFilePipelineSettings(
					localStorage.token,
					Object.fromEntries(changed.map((key) => [key, values[key]]))
				)
			);
			return true;
		} catch (error) {
			toast.error(`${error}`);
			return false;
		}
	};

	// Same rule as the monitor: shown only while every listed field has one of the given values.
	const visible = (setting: Setting, current: Record<string, any>) =>
		Object.entries(setting.show_if ?? {}).every(
			([key, allowed]) =>
				!(key in current) ||
				allowed.some((v) =>
					typeof v === 'boolean' ? v === current[key] : String(v) === String(current[key] ?? '')
				)
		);

	const isDefault = (setting: Setting, value: any) =>
		value === null || value === '' || String(value) === String(setting.default ?? '');

	const inSection = (all: Setting[], id: string, advanced: boolean) =>
		all.filter((s) => (s.section ?? '') === id && !!s.advanced === advanced);

	$: changed = Object.keys(values).filter((key) => values[key] !== saved[key]);

	onMount(load);
</script>

{#if settings.length}
	<AdminSettingSection {title}>
		<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
			{note}
			{#if changed.length}
				<span class="text-amber-600 dark:text-amber-400">
					· {changed.length} thay đổi chưa lưu, bấm Lưu ở cuối trang.
				</span>
			{/if}
		</p>

		{#each sections as section (section.id)}
			{@const main = inSection(settings, section.id, false).filter((s) => visible(s, values))}
			{@const extra = inSection(settings, section.id, true).filter((s) => visible(s, values))}
			{@const edited = extra.filter((s) => !isDefault(s, values[s.key])).length}
			{#if main.length || extra.length}
				<div class="mt-2 flex flex-col gap-2.5 rounded-xl border border-gray-100 p-3 dark:border-gray-850">
					{#if section.title}
						<div>
							<div class="text-xs font-medium text-gray-700 dark:text-gray-300">{section.title}</div>
							{#if section.note}
								<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">{section.note}</div>
							{/if}
						</div>
					{/if}

					{#each open[section.id] ? [...main, ...extra] : main as setting (setting.key)}
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

					{#if extra.length}
						<button
							type="button"
							class="self-start text-[0.6875rem] text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 transition"
							on:click={() => (open = { ...open, [section.id]: !open[section.id] })}
						>
							{open[section.id] ? '▾ Ẩn thiết lập nâng cao' : `▸ Thiết lập nâng cao (${extra.length})`}
							{#if !open[section.id] && edited}
								<span class="text-amber-600 dark:text-amber-400">· {edited} khác mặc định</span>
							{/if}
						</button>
					{/if}
				</div>
			{/if}
		{/each}
	</AdminSettingSection>
{/if}
