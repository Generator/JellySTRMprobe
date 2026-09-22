using System;
using System.Linq;
using FluentAssertions;
using Xunit;

namespace JellySTRMprobe.Tests;

public class PluginTests
{
    public PluginTests()
    {
        TestHelpers.EnsurePluginInstance();
    }

    [Fact]
    public void Plugin_HasCorrectName()
    {
        Plugin.Instance.Name.Should().Be("strmprobe");
    }

    [Fact]
    public void Plugin_HasValidGuid()
    {
        Plugin.Instance.Id.Should().NotBe(Guid.Empty);
        Plugin.Instance.Id.Should().Be(Guid.Parse("f203eaa8-4a6f-46b5-a9c4-eebbbd68a787"));
    }

    [Fact]
    public void Plugin_ReturnsConfigPages()
    {
        var pages = Plugin.Instance.GetPages().ToList();

        pages.Should().HaveCount(2);
        pages.Should().Contain(p => p.Name == "strmprobe.html");
        pages.Should().Contain(p => p.Name == "strmprobe.js");
    }
}
