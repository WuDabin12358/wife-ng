package cn.wudabin.wifeng.mixin;

import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.render.GameRenderer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Prevent every graphics-only render path in a HeadlessMC process. */
@Mixin(GameRenderer.class)
public abstract class HeadlessGameRendererMixin {
    @Inject(method = "render", at = @At("HEAD"), cancellable = true)
    private void wifeNg$skipRendering(float tickDelta, long startTime, boolean tick,
                                      CallbackInfo callbackInfo) {
        if (FabricLoader.getInstance().isModLoaded("headlessmc")) {
            callbackInfo.cancel();
        }
    }
}
