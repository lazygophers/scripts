package com.lazygophers.lazygit;

import com.intellij.openapi.options.Configurable;
import com.intellij.openapi.options.ConfigurableGroup;
import com.intellij.openapi.options.ShowSettingsUtil;
import com.intellij.openapi.project.Project;
import java.awt.Component;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;
import java.util.function.Predicate;

/** 记录 showSettingsDialog(project, id) 调用的空设置页工具。Java 写以避开 Kotlin 的精确 nullability。 */
public class FakeShowSettingsUtil extends ShowSettingsUtil {
    public final List<String> dialogCalls = new ArrayList<>();

    @Override
    public void showSettingsDialog(Project project, ConfigurableGroup... group) { }

    @Override
    public <T extends Configurable> void showSettingsDialog(Project project, Class<T> configurableClass) { }

    @Override
    public void showSettingsDialog(Project project, String nameToSelect) {
        dialogCalls.add(nameToSelect);
    }

    @Override
    public void showSettingsDialog(Project project, Configurable configurable) { }

    @Override
    public <T extends Configurable> void showSettingsDialog(Project project, Class<T> configurableClass, Consumer<? super T> consumer) { }

    @Override
    public void showSettingsDialog(Project project, Predicate<? super Configurable> predicate, Consumer<? super Configurable> consumer) { }

    @Override
    public boolean editConfigurable(Project project, Configurable configurable) { return true; }

    @Override
    public boolean editConfigurable(Project project, Configurable configurable, Runnable onApply) { return true; }

    @Override
    public <T extends Configurable> boolean editConfigurable(Project project, T configurable, Consumer<? super T> consumer) { return true; }

    @Override
    public boolean editConfigurable(Component parent, Configurable configurable) { return true; }

    @Override
    public boolean editConfigurable(Component parent, String nameToSelect) { return true; }

    @Override
    public boolean editConfigurable(Component parent, String nameToSelect, Runnable onApply) { return true; }

    @Override
    public boolean editConfigurable(Component parent, String nameToSelect, Configurable configurable) { return true; }

    @Override
    public boolean editConfigurable(Component parent, Configurable configurable, Runnable onApply) { return true; }

    @Override
    public boolean editConfigurable(Project project, String nameToSelect, Configurable configurable) { return true; }

    @Override
    public boolean editConfigurable(Project project, String nameToSelect, Configurable configurable, boolean isTabFocused) { return true; }
}
