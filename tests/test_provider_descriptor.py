import unittest

from ciel_runtime_support.provider_adapters import PROVIDER_ADAPTERS, PROVIDER_DESCRIPTORS, PROVIDER_LABELS
from ciel_runtime_support.provider_descriptor import ProviderDescriptor, ProviderDescriptorRegistry
from ciel_runtime_support.providers.vllm import VllmProviderAdapter


class ProviderDescriptorTests(unittest.TestCase):
    def test_descriptor_registry_is_adapter_registry_source(self):
        self.assertEqual(PROVIDER_DESCRIPTORS.names(), PROVIDER_ADAPTERS.names())
        self.assertEqual(
            PROVIDER_LABELS,
            {descriptor.normalized_name: descriptor.label for descriptor in PROVIDER_DESCRIPTORS.descriptors()},
        )

    def test_descriptor_constructs_default_and_configured_adapter(self):
        descriptor = ProviderDescriptor("example", "Example", VllmProviderAdapter)
        self.assertIsInstance(descriptor.create(), VllmProviderAdapter)
        self.assertEqual("https://example.invalid", descriptor.create(base_url="https://example.invalid").default_base_url())

    def test_alias_lookup_is_normalized(self):
        descriptor = ProviderDescriptor("example-hosted", "Example", VllmProviderAdapter, aliases=("example_cloud",))
        registry = ProviderDescriptorRegistry((descriptor,))
        self.assertIs(descriptor, registry.get("EXAMPLE-CLOUD"))
        self.assertEqual("example-hosted", registry.aliases()["example-cloud"])

    def test_duplicate_alias_is_rejected(self):
        with self.assertRaises(ValueError):
            ProviderDescriptorRegistry(
                (
                    ProviderDescriptor("one", "One", VllmProviderAdapter, aliases=("shared",)),
                    ProviderDescriptor("two", "Two", VllmProviderAdapter, aliases=("shared",)),
                )
            )


if __name__ == "__main__":
    unittest.main()
